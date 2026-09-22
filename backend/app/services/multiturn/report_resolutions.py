"""历史评分完成后独立判断问题现状，结果不参与原分数计算。"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.base import ModelClient
from app.models.conversation import ConversationJudgeRun
from app.services.multiturn.issue_resolution import IssueResolution, aggregate_resolution
from app.services.multiturn.assessment_batches import batch_count
from app.services.multiturn.resolution_store import ResolutionStore, execute_resolution
from app.services.multiturn.store import ConversationError
from app.services.token_quota_service import TokenQuotaExceededError


async def resolve_report_issues(sessions: async_sessionmaker[AsyncSession], job_id: int, owner: int,
                               client: ModelClient, *, output_tokens: int) -> None:
    """逐问题执行三组独立状态判断并一次保存汇总，重复作业不会再次发送。"""
    store = ResolutionStore()
    async with sessions() as db:
        job = await store._job(db, job_id, owner)
        if job.input_json["report"].get("resolutionComplete"):
            return
        rows = (await db.scalars(select(ConversationJudgeRun).where(ConversationJudgeRun.assessment_id == job_id)
            .order_by(ConversationJudgeRun.run_index))).all()
        expected = 3 * batch_count(job.input_json)
        if {row.run_index for row in rows} != set(range(1, expected + 1)) or any(row.status == "pending" for row in rows):
            raise ConversationError("resolution_state_conflict", "三组历史评审尚未完整保存，不能汇总问题状态", 409)
        failures = {item["id"] for row in rows if row.status == "provisional"
            for item in (row.result_json or {}).get("items", []) if item.get("applicability") == "applicable"
            and ((item.get("rating") is not None and item["rating"] < 3) or item.get("passed") is False)}
        checks = {check["id"]: check for check in job.input_json["packet"]["checks"]}
        through_turn = job.through_turn
    results: list[dict[str, object]] = []
    for issue_id in sorted(failures):
        entry: dict[str, object] = {"issueId": issue_id, "description": checks[issue_id]["description"],
            "throughTurn": through_turn, "status": "unknown", "reason": "尚未取得一致状态", "reviews": []}
        try:
            async with sessions() as db:
                packet = await store.packet(db, job_id, owner, issue_id)
        except ConversationError as error:
            if error.code not in ("resolution_failure_disputed", "resolution_anchor_missing"):
                raise
            entry.update(reason=str(error), errorCode=error.code)
            results.append(entry)
            continue
        except ValueError as error:
            entry.update(reason=str(error), errorCode="resolution_material_unavailable")
            results.append(entry)
            continue
        runs: list[IssueResolution | None] = []
        reviews: list[dict[str, object]] = []
        for index in range(1, 4):
            try:
                result = await execute_resolution(sessions, job_id, owner, issue_id, index, client, output_tokens=output_tokens)
            except TokenQuotaExceededError:
                entry.update(reason="状态评审额度不足，未执行的组保持未知", errorCode="resolution_quota_exceeded")
                reviews.extend({"reviewIndex": pending, "valid": False, "status": "unknown",
                    "reason": "额度不足，本组未执行", "errorCode": "resolution_not_run", "evidence": []}
                    for pending in range(index, 4))
                break
            runs.append(result.result)
            reviews.append({"reviewIndex": index, "valid": result.result is not None,
                "status": result.result.status if result.result else "unknown",
                "reason": result.result.reason if result.result else "本组未获得有效结论",
                "errorCode": result.error_code,
                "evidence": [ref.model_dump(mode="json") for ref in result.result.evidence] if result.result else []})
        completed = len(runs) == 3
        entry["status"] = aggregate_resolution(tuple(runs), packet) if completed else "unknown"
        entry["reviews"] = reviews
        if completed:
            entry["reason"] = "有效组结论一致" if entry["status"] != "unknown" else "状态未知、有效组不足或组间分歧"
        results.append(entry)
    async with sessions() as db:
        job = await store._job(db, job_id, owner)
        if job.input_json["report"].get("resolutionComplete"):
            raise ConversationError("resolution_state_conflict", "问题状态已经固定", 409)
        job.input_json = {**job.input_json, "report": {**job.input_json["report"],
            "issueResolutions": results, "resolutionComplete": True}}
        await db.commit()
