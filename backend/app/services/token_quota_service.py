from datetime import date, datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evaluation import EvaluationTask
from app.models.conversation import ConversationUsage
from app.models.rag import RagResponseDetail
from app.models.response import ModelResponse
from app.models.token_usage import DailyUserTokenUsage, TokenUsageLog, UserTokenQuota
from app.models.user import User
from app.schemas.token_usage import AdminUserListRead, AdminUserUsageRead, TokenUsageRead
from app.schemas.rag import RagStageUsage
from app.services.rag.usage import summarize_usage

DEFAULT_DAILY_TOKEN_LIMIT = 100_000
SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")


class TokenQuotaExceededError(Exception):
    pass


class TokenQuotaUserError(Exception):
    pass


class TokenQuotaService:
    def usage_date(self, now: datetime | None = None) -> date:
        current = now or datetime.now(tz=SHANGHAI_TIMEZONE)
        if current.tzinfo is None:
            current = current.replace(tzinfo=SHANGHAI_TIMEZONE)
        return current.astimezone(SHANGHAI_TIMEZONE).date()

    async def get_today_usage(self, db: AsyncSession, user: User) -> TokenUsageRead:
        usage_date = self.usage_date()
        used_tokens, daily_limit = await self._usage_and_limit(db, user.id, usage_date)
        if user.role == "admin":
            return TokenUsageRead(
                usageDate=usage_date,
                usedTokens=used_tokens,
                dailyLimit=None,
                remainingTokens=None,
                unlimited=True,
            )

        return TokenUsageRead(
            usageDate=usage_date,
            usedTokens=used_tokens,
            dailyLimit=daily_limit,
            remainingTokens=max(daily_limit - used_tokens, 0),
            unlimited=False,
        )

    async def ensure_can_start(self, db: AsyncSession, user: User) -> None:
        usage = await self.get_today_usage(db, user)
        if not usage.unlimited and usage.remaining_tokens == 0:
            raise TokenQuotaExceededError("今日 Token 额度已用完，请明日再试或联系管理员调整额度")

    async def record_usage(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        task_id: int,
        response_id: int,
        model_config_id: int | None,
        total_tokens: int,
    ) -> None:
        normalized_tokens = max(total_tokens, 0)
        usage_date = self.usage_date()
        db.add(
            TokenUsageLog(
                user_id=user_id,
                task_id=task_id,
                response_id=response_id,
                model_config_id=model_config_id,
                usage_date=usage_date,
                total_tokens=normalized_tokens,
            )
        )
        statement = mysql_insert(DailyUserTokenUsage).values(
            user_id=user_id,
            usage_date=usage_date,
            total_tokens=normalized_tokens,
        )
        statement = statement.on_duplicate_key_update(
            total_tokens=DailyUserTokenUsage.total_tokens + normalized_tokens
        )
        await db.execute(statement)

    async def record_rag_usage(self, db: AsyncSession, *, response_id: int, user_id: int) -> bool:
        """调用者在评分终态事务内调用；回答锁与唯一日志共同防止重复累计。"""
        response = await db.scalar(select(ModelResponse).join(EvaluationTask).where(
            ModelResponse.id == response_id, EvaluationTask.user_id == user_id,
            EvaluationTask.task_type == "rag",
        ).with_for_update().execution_options(populate_existing=True))
        if response is None or response.status not in ("success", "failed"):
            raise ValueError("RAG 回答尚未结束或无权访问，不能汇总入账")
        # 调用方可能已建立 REPEATABLE READ 快照；锁后检查也必须读取当前提交值。
        existing = await db.scalar(select(TokenUsageLog.id).where(TokenUsageLog.response_id == response_id).with_for_update())
        if existing is not None:
            return False
        detail = await db.scalar(select(RagResponseDetail).where(RagResponseDetail.response_id == response_id)
                                 .with_for_update().execution_options(populate_existing=True))
        if detail is None:
            raise ValueError("RAG 回答缺少阶段用量明细")
        stages = [RagStageUsage.model_validate(item) for item in detail.stage_usage_json]
        for stage in stages:
            if stage.status == "pending":
                stage.status = "unknown"
        detail.stage_usage_json = [stage.model_dump(mode="json", by_alias=True) for stage in stages]
        summary = summarize_usage(stages)
        await self.record_usage(db, user_id=user_id, task_id=response.task_id, response_id=response_id,
            model_config_id=response.model_config_id, total_tokens=summary.external_total_tokens)
        await db.flush()
        return True

    async def record_conversation_usage(self, db: AsyncSession, *, usage_id: int, user_id: int) -> bool:
        """阶段流水与日用量在同事务幂等入账，未知用量保留待核对。"""
        usage = await db.scalar(select(ConversationUsage).where(
            ConversationUsage.id == usage_id, ConversationUsage.user_id == user_id,
        ).with_for_update().execution_options(populate_existing=True))
        if usage is None:
            raise ValueError("阶段用量不存在或无权访问")
        if usage.accounted:
            return False
        if usage.status != "completed" or usage.total_tokens is None:
            return False
        if type(usage.total_tokens) is not int or usage.total_tokens < 0:
            raise ValueError("阶段用量必须为非负整数")
        # 数据库存储 UTC 无时区时间，不能交给默认按北京时间解释的 usage_date。
        started = usage.created_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        day = self.usage_date(started)
        statement = mysql_insert(DailyUserTokenUsage).values(
            user_id=user_id, usage_date=day, total_tokens=usage.total_tokens)
        await db.execute(statement.on_duplicate_key_update(
            total_tokens=DailyUserTokenUsage.total_tokens + usage.total_tokens))
        usage.accounted = True
        usage.detail_json = {**usage.detail_json, "usageDate": day.isoformat()}
        await db.flush()
        return True

    async def list_users(
        self,
        db: AsyncSession,
        *,
        page: int = 1,
        page_size: int = 10,
        keyword: str | None = None,
        role: Literal["user", "admin"] | None = None,
        user_status: Literal["active", "disabled"] | None = None,
    ) -> AdminUserListRead:
        usage_date = self.usage_date()
        filters = [User.id != 0]
        normalized_keyword = keyword.strip() if keyword else None
        if normalized_keyword:
            filters.append(User.username.ilike(f"%{normalized_keyword}%"))
        if role:
            filters.append(User.role == role)
        if user_status:
            filters.append(User.status == user_status)

        total_result = await db.execute(select(func.count(User.id)).where(*filters))
        total = int(total_result.scalar_one())

        rows_result = await db.execute(
            select(
                User,
                func.coalesce(DailyUserTokenUsage.total_tokens, 0),
                UserTokenQuota.daily_limit,
            )
            .outerjoin(
                DailyUserTokenUsage,
                (DailyUserTokenUsage.user_id == User.id)
                & (DailyUserTokenUsage.usage_date == usage_date),
            )
            .outerjoin(UserTokenQuota, UserTokenQuota.user_id == User.id)
            .where(*filters)
            .order_by(User.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return AdminUserListRead(
            items=[
                self._admin_user_usage_read(
                    user=user,
                    usage_date=usage_date,
                    used_tokens=int(used_tokens),
                    daily_limit=None if daily_limit is None else int(daily_limit),
                )
                for user, used_tokens, daily_limit in rows_result.all()
            ],
            total=total,
            page=page,
            pageSize=page_size,
        )

    async def set_user_status(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        user_status: Literal["active", "disabled"],
        operator_user_id: int,
    ) -> AdminUserUsageRead:
        if user_id == 0:
            raise TokenQuotaUserError("不能修改系统匿名用户状态")
        if user_id == operator_user_id:
            raise TokenQuotaUserError("不能封禁当前登录的管理员账号")

        result = await db.execute(select(User).where(User.id == user_id, User.id != 0))
        user = result.scalar_one_or_none()
        if user is None:
            raise TokenQuotaUserError("用户不存在")

        user.status = user_status
        await db.commit()
        return await self.get_admin_user_usage(db, user_id)

    async def get_admin_user_usage(self, db: AsyncSession, user_id: int) -> AdminUserUsageRead:
        usage_date = self.usage_date()
        result = await db.execute(
            select(
                User,
                func.coalesce(DailyUserTokenUsage.total_tokens, 0),
                UserTokenQuota.daily_limit,
            )
            .select_from(User)
            .outerjoin(
                DailyUserTokenUsage,
                (DailyUserTokenUsage.user_id == User.id)
                & (DailyUserTokenUsage.usage_date == usage_date),
            )
            .outerjoin(UserTokenQuota, UserTokenQuota.user_id == User.id)
            .where(User.id == user_id)
        )
        user, used_tokens, daily_limit = result.one()
        return self._admin_user_usage_read(
            user=user,
            usage_date=usage_date,
            used_tokens=int(used_tokens),
            daily_limit=None if daily_limit is None else int(daily_limit),
        )

    async def set_user_quota(
        self,
        db: AsyncSession,
        user_id: int,
        daily_limit: int,
    ) -> AdminUserUsageRead:
        result = await db.execute(select(User).where(User.id == user_id, User.id != 0))
        user = result.scalar_one_or_none()
        if user is None:
            raise TokenQuotaUserError("用户不存在")
        if user.role == "admin":
            raise TokenQuotaUserError("管理员账号不受每日 Token 额度限制")

        statement = mysql_insert(UserTokenQuota).values(user_id=user_id, daily_limit=daily_limit)
        statement = statement.on_duplicate_key_update(daily_limit=daily_limit)
        await db.execute(statement)
        await db.commit()

        return await self.get_admin_user_usage(db, user.id)

    async def task_user_id(self, db: AsyncSession, task_id: int) -> int:
        result = await db.execute(select(EvaluationTask.user_id).where(EvaluationTask.id == task_id))
        return int(result.scalar_one())

    async def _usage_and_limit(
        self,
        db: AsyncSession,
        user_id: int,
        usage_date: date,
    ) -> tuple[int, int]:
        result = await db.execute(
            select(
                func.coalesce(DailyUserTokenUsage.total_tokens, 0),
                UserTokenQuota.daily_limit,
            )
            .select_from(User)
            .outerjoin(
                DailyUserTokenUsage,
                (DailyUserTokenUsage.user_id == User.id)
                & (DailyUserTokenUsage.usage_date == usage_date),
            )
            .outerjoin(UserTokenQuota, UserTokenQuota.user_id == User.id)
            .where(User.id == user_id)
        )
        used_tokens, daily_limit = result.one()
        resolved_limit = DEFAULT_DAILY_TOKEN_LIMIT if daily_limit is None else daily_limit
        return int(used_tokens), int(resolved_limit)

    def _admin_user_usage_read(
        self,
        *,
        user: User,
        usage_date: date,
        used_tokens: int,
        daily_limit: int | None,
    ) -> AdminUserUsageRead:
        return AdminUserUsageRead(
            id=user.id,
            username=user.username,
            role=user.role,
            status=user.status,
            usageDate=usage_date,
            usedTokens=used_tokens,
            dailyLimit=(
                None
                if user.role == "admin"
                else int(DEFAULT_DAILY_TOKEN_LIMIT if daily_limit is None else daily_limit)
            ),
        )


token_quota_service = TokenQuotaService()
