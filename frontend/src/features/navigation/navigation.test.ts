import { describe, expect, test } from "vitest";

import { getVisibleNavigationItems, resolveRouteAccess } from "./navigation";
import type { UserProfile } from "../../api/client";

const regularUser: UserProfile = {
  id: 1,
  username: "user",
  role: "user",
  status: "active"
};

const adminUser: UserProfile = {
  id: 2,
  username: "admin",
  role: "admin",
  status: "active"
};

describe("React 阶段二导航权限", () => {
  test("普通用户不显示管理员入口", () => {
    const labels = getVisibleNavigationItems(regularUser).map((item) => item.label);

    expect(labels).toEqual(["评测工作台", "RAG 评测", "知识库", "历史任务", "反馈统计"]);
  });

  test("管理员显示模型配置、用户额度和评分配置入口", () => {
    const labels = getVisibleNavigationItems(adminUser).map((item) => item.label);

    expect(labels).toEqual(["评测工作台", "RAG 评测", "知识库", "模型配置", "用户额度", "评分配置", "历史任务", "反馈统计"]);
  });

  test("未登录访问业务页进入登录流程", () => {
    expect(resolveRouteAccess("/", null)).toEqual({ type: "redirect", to: "/login", redirect: "/" });
    expect(resolveRouteAccess("/history", null)).toEqual({ type: "redirect", to: "/login", redirect: "/history" });
  });

  test("普通用户访问管理员页回到工作台", () => {
    expect(resolveRouteAccess("/models", regularUser)).toEqual({ type: "redirect", to: "/" });
    expect(resolveRouteAccess("/users", regularUser)).toEqual({ type: "redirect", to: "/" });
    expect(resolveRouteAccess("/scoring-rules", regularUser)).toEqual({ type: "redirect", to: "/" });
  });

  test("已登录用户访问登录页回到工作台", () => {
    expect(resolveRouteAccess("/login", regularUser)).toEqual({ type: "redirect", to: "/" });
    expect(resolveRouteAccess("/register", adminUser)).toEqual({ type: "redirect", to: "/" });
  });
});
