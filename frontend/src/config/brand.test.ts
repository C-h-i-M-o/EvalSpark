import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, test, vi } from "vitest";

import { AppLayout } from "../layout/AppLayout";
import { AuthPage } from "../pages/AuthPage";

vi.mock("../features/auth/AuthContext", () => ({
  useAuth: () => ({
    user: {
      id: 1,
      username: "admin",
      role: "admin",
      status: "active"
    },
    initialized: true,
    loading: false,
    login: async () => ({ id: 1, username: "admin", role: "admin", status: "active" }),
    register: async () => ({ id: 1, username: "admin", role: "admin", status: "active" }),
    logout: async () => undefined,
    refresh: async () => ({ id: 1, username: "admin", role: "admin", status: "active" })
  })
}));

describe("React 品牌展示", () => {
  test("认证页和应用布局统一显示 EvalSpark", () => {
    const authMarkup = renderToStaticMarkup(
      createElement(MemoryRouter, null, createElement(AuthPage, { mode: "login" }))
    );
    const layoutMarkup = renderToStaticMarkup(
      createElement(MemoryRouter, null, createElement(AppLayout))
    );
    const visibleMarkup = `${authMarkup}${layoutMarkup}`;

    expect(authMarkup).toContain(">EvalSpark<");
    expect(layoutMarkup).toContain(">EvalSpark<");
    expect(visibleMarkup).toContain('alt="EvalSpark 标志"');
    expect(visibleMarkup).not.toContain("MultiChatEval");
  });
});
