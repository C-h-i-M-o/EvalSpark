import type { UserProfile } from "../../api/client";

export interface NavigationItem {
  path: string;
  label: string;
  adminOnly?: boolean;
}

export interface NavigationGroup {
  key: string;
  label: string;
  path?: string;
  items: NavigationItem[];
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
  { path: "/embedding-config", label: "Embedding 配置", adminOnly: true },
  { path: "/users", label: "用户额度", adminOnly: true },
  { path: "/scoring-rules", label: "评分配置", adminOnly: true },
  { path: "/history", label: "历史任务" },
  { path: "/rag/history", label: "RAG 历史任务" },
  { path: "/feedback", label: "反馈统计" }
];

export const navigationGroups: NavigationGroup[] = [
  { key: "evaluation", label: "普通评测", items: navigationItems.filter((item) => ["/", "/history"].includes(item.path)) },
  { key: "rag", label: "RAG 评测", items: navigationItems.filter((item) => ["/rag", "/knowledge-bases", "/rag/history"].includes(item.path)) },
  { key: "agent", label: "Agent 评测", items: [] },
  { key: "feedback", label: "反馈统计", path: "/feedback", items: [] },
  { key: "settings", label: "系统设置", adminOnly: true, items: navigationItems.filter((item) => ["/models", "/users", "/scoring-rules"].includes(item.path)) }
];

const publicRoutes = new Set(["/login", "/register"]);
const adminRoutes = new Set(["/models", "/embedding-config", "/users", "/scoring-rules"]);

export function getVisibleNavigationItems(user: UserProfile): NavigationItem[] {
  return navigationItems.filter((item) => !item.adminOnly || user.role === "admin");
}

export function getVisibleNavigationGroups(user: UserProfile): NavigationGroup[] {
  return navigationGroups
    .filter((group) => !group.adminOnly || user.role === "admin")
    .map((group) => ({ ...group, items: getVisibleNavigationItems(user).filter((item) => group.items.some((groupItem) => groupItem.path === item.path)) }));
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
