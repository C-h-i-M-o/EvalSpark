import type { UserProfile } from "../../api/client";

export interface NavigationItem {
  path: string;
  label: string;
  adminOnly?: boolean;
}

export type RouteAccess =
  | { type: "allow" }
  | { type: "redirect"; to: string; redirect?: string };

export const navigationItems: NavigationItem[] = [
  { path: "/", label: "评测工作台" },
  { path: "/rag", label: "RAG 评测" },
  { path: "/knowledge-bases", label: "知识库" },
  { path: "/models", label: "模型配置", adminOnly: true },
  { path: "/users", label: "用户额度", adminOnly: true },
  { path: "/scoring-rules", label: "评分配置", adminOnly: true },
  { path: "/history", label: "历史任务" },
  { path: "/feedback", label: "反馈统计" }
];

const publicRoutes = new Set(["/login", "/register"]);
const adminRoutes = new Set(["/models", "/users", "/scoring-rules"]);

export function getVisibleNavigationItems(user: UserProfile): NavigationItem[] {
  return navigationItems.filter((item) => !item.adminOnly || user.role === "admin");
}

export function resolveRouteAccess(pathname: string, user: UserProfile | null): RouteAccess {
  const normalizedPath = normalizePath(pathname);

  if (publicRoutes.has(normalizedPath)) {
    return user ? { type: "redirect", to: "/" } : { type: "allow" };
  }

  if (!user) {
    return { type: "redirect", to: "/login", redirect: normalizedPath };
  }

  if (adminRoutes.has(normalizedPath) && user.role !== "admin") {
    return { type: "redirect", to: "/" };
  }

  return { type: "allow" };
}

function normalizePath(pathname: string): string {
  if (pathname === "") {
    return "/";
  }

  const pathWithoutQuery = pathname.split("?")[0] || "/";
  return pathWithoutQuery.length > 1 && pathWithoutQuery.endsWith("/")
    ? pathWithoutQuery.slice(0, -1)
    : pathWithoutQuery;
}
