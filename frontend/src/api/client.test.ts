import { afterEach, describe, expect, test, vi } from "vitest";

import {
  ApiError,
  createModelConfig,
  getCurrentUser,
  getAdminFeedbackStats,
  getHealthStatus,
  getEvaluationTask,
  getPersonalFeedbackStats,
  listEvaluationTasks,
  listAdminUsers,
  listRuleTerms,
  listModelConfigs,
  listResponseComments,
  loginUser,
  logoutUser,
  createResponseComment,
  deleteModelConfig,
  deleteResponseComment,
  registerUser,
  streamEvaluationTask,
  submitResponseFeedback,
  testModelConfig,
  updateAdminUserStatus,
  updateEvaluationTaskVisibility,
  updateModelConfig,
  updateUserQuota
} from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

function mockJsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    headers: {
      "Content-Type": "application/json"
    }
  });
}

describe("React 阶段一 API 客户端", () => {
  test("历史可见性通过带登录态的 PATCH 保存，并保留权限错误", async () => {
    const task = { taskId: 42, visibility: "public" };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(mockJsonResponse(task))
      .mockResolvedValueOnce(mockJsonResponse({ detail: "任务不存在" }, { status: 404 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(updateEvaluationTaskVisibility(42, "public")).resolves.toEqual(task);
    expect(fetchMock).toHaveBeenCalledWith("/api/evaluation/tasks/42/visibility", {
      method: "PATCH",
      credentials: "include",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ visibility: "public" })
    });
    await expect(updateEvaluationTaskVisibility(42, "private")).rejects.toMatchObject({ status: 404 });
  });
  test("读取后端健康检查", async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse({ status: "ok" }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getHealthStatus()).resolves.toEqual({ status: "ok" });

    expect(fetchMock).toHaveBeenCalledWith("/api/health", {
      credentials: "include",
      headers: {
        Accept: "application/json"
      }
    });
  });

  test("读取当前登录用户", async () => {
    const user = { id: 1, username: "admin", role: "admin", status: "active" };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockJsonResponse(user)));

    await expect(getCurrentUser()).resolves.toEqual(user);
  });

  test("非 2xx 响应抛出带状态码的错误", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockJsonResponse({ detail: "未登录" }, { status: 401 })));

    await expect(getCurrentUser()).rejects.toEqual(new ApiError(401, "未登录"));
  });

  test("FastAPI 校验错误不会显示为对象字符串", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        mockJsonResponse(
          {
            detail: [
              {
                loc: ["body", "password"],
                msg: "String should have at least 8 characters",
                type: "string_too_short"
              }
            ]
          },
          { status: 422 }
        )
      )
    );

    await expect(loginUser({ username: "test", password: "123" })).rejects.toEqual(
      new ApiError(422, "密码长度不能少于 8 位")
    );
  });
});

describe("React 阶段二认证 API 客户端", () => {
  test("登录请求使用 HttpOnly Cookie 会话语义", async () => {
    const user = { id: 2, username: "demo", role: "user", status: "active" };
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse(user));
    vi.stubGlobal("fetch", fetchMock);

    await expect(loginUser({ username: "demo", password: "password123" })).resolves.toEqual(user);

    expect(fetchMock).toHaveBeenCalledWith("/api/auth/login", {
      method: "POST",
      credentials: "include",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ username: "demo", password: "password123" })
    });
  });

  test("注册请求返回当前用户", async () => {
    const user = { id: 3, username: "newuser", role: "user", status: "active" };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(mockJsonResponse(user, { status: 201 })));

    await expect(
      registerUser({ username: "newuser", password: "Password123", confirmPassword: "Password123" })
    ).resolves.toEqual(user);
  });

  test("退出请求不解析空响应体", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(logoutUser()).resolves.toBeUndefined();

    expect(fetchMock).toHaveBeenCalledWith("/api/auth/logout", {
      method: "POST",
      credentials: "include",
      headers: {
        Accept: "application/json"
      }
    });
  });
});

describe("React 阶段三评测 API 客户端", () => {
  test("按 NDJSON 分片顺序推送流式事件", async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('{"type":"task_started","taskId":7,"prompt":"问题","status":"running"}\n{"type":"model_delta","modelConfigId":2,"delta":"第一段"}\n{"type":"model_answer_completed","modelConfigId":2}\n{"type":"model_'));
        controller.enqueue(
          encoder.encode(
            'response","response":{"id":11,"modelConfigId":2,"modelName":"DeepSeek","provider":"deepseek","answer":"答案","latencyMs":1200,"inputTokens":3,"outputTokens":4,"cacheHitTokens":0,"cacheCreationTokens":0,"totalTokens":7,"estimatedCost":0.01,"currency":"CNY","costDetails":{"inputCost":0,"outputCost":0.01,"cacheHitCost":0,"cacheCreationCost":0},"status":"success","score":{"relevance":9,"completeness":8,"clarity":8,"format":7,"safety":10,"final":8.5,"details":{},"ruleFinal":8.5,"judgeFinal":null,"baseFinal":8.5,"feedbackScore":null,"judgeComment":null,"judgeDetails":{}},"feedback":{"liked":false,"likeCount":0,"disliked":false,"dislikeCount":0}}}\n'
          )
        );
        controller.close();
      }
    });
    const fetchMock = vi.fn().mockResolvedValue(new Response(stream, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const events: unknown[] = [];

    await streamEvaluationTask(
      {
        prompt: "问题",
        modelIds: [2],
        enableJudge: false,
        judgeModelId: null,
        enableThinking: true,
        visibility: "public"
      },
      (event) => events.push(event)
    );

    expect(fetchMock).toHaveBeenCalledWith("/api/evaluation/tasks/stream", {
      method: "POST",
      credentials: "include",
      headers: {
        Accept: "application/x-ndjson",
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        prompt: "问题",
        modelIds: [2],
        enableJudge: false,
        judgeModelId: null,
        enableThinking: true,
        visibility: "public"
      })
    });
    expect(events).toHaveLength(4);
    expect(events[0]).toMatchObject({ type: "task_started", taskId: 7 });
    expect(events[1]).toMatchObject({ type: "model_delta", modelConfigId: 2, delta: "第一段" });
    expect(events[2]).toMatchObject({ type: "model_answer_completed", modelConfigId: 2 });
    expect(events[3]).toMatchObject({ type: "model_response", response: { id: 11, modelName: "DeepSeek" } });
  });

  test("提交反馈使用后端 feedbackType 字段", async () => {
    const result = {
      responseId: 11,
      feedbackType: "like",
      active: true,
      feedback: { liked: true, likeCount: 1, disliked: false, dislikeCount: 0 },
      score: { relevance: 9, completeness: 8, clarity: 8, format: 7, safety: 10, final: 8.6, details: {} }
    };
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse(result));
    vi.stubGlobal("fetch", fetchMock);

    await expect(submitResponseFeedback(11, "like")).resolves.toEqual(result);

    expect(fetchMock).toHaveBeenCalledWith("/api/evaluation/responses/11/feedback", {
      method: "POST",
      credentials: "include",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ feedbackType: "like" })
    });
  });
});

describe("React 阶段四历史与评论 API 客户端", () => {
  test("分页读取历史任务时使用后端 pageSize 参数", async () => {
    const payload = {
      items: [
        {
          taskId: 9,
          status: "completed",
          prompt: "历史问题",
          createdAt: "2026-07-03T04:00:00",
          completedAt: "2026-07-03T04:01:00",
          responseCount: 2,
          ownerId: 1,
          ownerUsername: "demo",
          visibility: "public"
        }
      ],
      total: 1,
      page: 2,
      pageSize: 20
    };
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse(payload));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listEvaluationTasks({ page: 2, pageSize: 20 })).resolves.toEqual(payload);

    expect(fetchMock).toHaveBeenCalledWith("/api/evaluation/tasks?page=2&pageSize=20", {
      credentials: "include",
      headers: {
        Accept: "application/json"
      }
    });
  });

  test("读取历史任务详情", async () => {
    const task = {
      taskId: 9,
      status: "completed",
      prompt: "历史问题",
      ownerUsername: "demo",
      visibility: "public",
      responses: []
    };
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse(task));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getEvaluationTask(9)).resolves.toEqual(task);

    expect(fetchMock).toHaveBeenCalledWith("/api/evaluation/tasks/9", {
      credentials: "include",
      headers: {
        Accept: "application/json"
      }
    });
  });

  test("评论查询、发布和删除使用既有后端接口", async () => {
    const commentList = {
      items: [
        {
          id: 3,
          responseId: 11,
          userId: 1,
          username: "demo",
          content: "这个回答更完整",
          createdAt: "2026-07-03T04:02:00",
          canDelete: true
        }
      ],
      total: 1,
      page: 1,
      pageSize: 10
    };
    const createdComment = commentList.items[0];
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(mockJsonResponse(commentList))
      .mockResolvedValueOnce(mockJsonResponse(createdComment, { status: 201 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listResponseComments(11, { page: 1, pageSize: 10 })).resolves.toEqual(commentList);
    await expect(createResponseComment(11, "这个回答更完整")).resolves.toEqual(createdComment);
    await expect(deleteResponseComment(3)).resolves.toBeUndefined();

    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/evaluation/responses/11/comments?page=1&pageSize=10", {
      credentials: "include",
      headers: {
        Accept: "application/json"
      }
    });
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/evaluation/responses/11/comments", {
      method: "POST",
      credentials: "include",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ content: "这个回答更完整" })
    });
    expect(fetchMock).toHaveBeenNthCalledWith(3, "/api/evaluation/comments/3", {
      method: "DELETE",
      credentials: "include",
      headers: {
        Accept: "application/json"
      }
    });
  });
});

describe("React 阶段四管理员 API 客户端", () => {
  const modelConfig = {
    id: 5,
    providerName: "deepseek",
    displayName: "DeepSeek",
    modelName: "deepseek-chat",
    baseUrl: "https://api.deepseek.com",
    enabled: true,
    hasApiKey: true,
    maskedApiKey: "sk-***",
    maxTokens: 1024,
    temperature: 0.7,
    timeoutSeconds: 60,
    notes: "",
    currency: "CNY" as const,
    priceInput: 1,
    priceOutput: 2,
    priceCacheHit: 0,
    priceCacheCreation: 0
  };

  test("模型配置列表、创建、更新、删除和测试使用管理员接口", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(mockJsonResponse([modelConfig]))
      .mockResolvedValueOnce(mockJsonResponse(modelConfig, { status: 201 }))
      .mockResolvedValueOnce(mockJsonResponse({ ...modelConfig, enabled: false }))
      .mockResolvedValueOnce(mockJsonResponse({ success: true, message: "连接成功", latencyMs: 88 }))
      .mockResolvedValueOnce(mockJsonResponse({ status: "deleted" }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listModelConfigs()).resolves.toEqual([modelConfig]);
    await expect(createModelConfig(modelConfig)).resolves.toEqual(modelConfig);
    await expect(updateModelConfig(5, { enabled: false })).resolves.toMatchObject({ enabled: false });
    await expect(testModelConfig({ modelConfigId: 5 })).resolves.toEqual({
      success: true,
      message: "连接成功",
      latencyMs: 88
    });
    await expect(deleteModelConfig(5)).resolves.toBeUndefined();

    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/admin/model-configs", {
      credentials: "include",
      headers: { Accept: "application/json" }
    });
    expect(fetchMock).toHaveBeenNthCalledWith(3, "/api/admin/model-configs/5", {
      method: "PUT",
      credentials: "include",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: false })
    });
    expect(fetchMock).toHaveBeenNthCalledWith(5, "/api/admin/model-configs/5", {
      method: "DELETE",
      credentials: "include",
      headers: { Accept: "application/json" }
    });
  });

  test("用户额度列表、筛选分页、封号和保存使用管理员接口", async () => {
    const user = {
      id: 2,
      username: "demo",
      role: "user",
      status: "active",
      usageDate: "2026-07-03",
      usedTokens: 1200,
      dailyLimit: 100000
    };
    const listResult = { items: [user], total: 1, page: 2, pageSize: 5 };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(mockJsonResponse(listResult))
      .mockResolvedValueOnce(mockJsonResponse({ ...user, status: "disabled" }))
      .mockResolvedValueOnce(mockJsonResponse({
        ...user,
        dailyLimit: 80000
      }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      listAdminUsers({ page: 2, pageSize: 5, keyword: "demo", role: "user", status: "active" })
    ).resolves.toEqual(listResult);
    await expect(updateAdminUserStatus(2, "disabled")).resolves.toMatchObject({ status: "disabled" });
    await expect(updateUserQuota(2, 80000)).resolves.toMatchObject({ dailyLimit: 80000 });

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/admin/users?page=2&pageSize=5&keyword=demo&role=user&status=active",
      {
        credentials: "include",
        headers: { Accept: "application/json" }
      }
    );
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/admin/users/2/status", {
      method: "PATCH",
      credentials: "include",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ status: "disabled" })
    });
    expect(fetchMock).toHaveBeenNthCalledWith(3, "/api/admin/users/2/quota", {
      method: "PUT",
      credentials: "include",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ dailyLimit: 80000 })
    });
  });

  test("反馈统计按用户角色接口读取", async () => {
    const stats = {
      scope: "personal",
      range: "30d",
      startAt: null,
      endAt: "2026-07-03T04:00:00",
      summary: { taskCount: 1, callCount: 2, scoredCount: 2, averageFinalScore: 8, likeCount: 1, dislikeCount: 0, likeRate: 1, commentCount: 1 },
      myInteractions: { likeCount: 1, dislikeCount: 0, commentCount: 1 },
      models: [],
      trend: []
    };
    const fetchMock = vi.fn().mockResolvedValueOnce(mockJsonResponse(stats)).mockResolvedValueOnce(mockJsonResponse({
      ...stats,
      scope: "global",
      activities: { items: [], total: 0, page: 1, pageSize: 20 }
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getPersonalFeedbackStats("30d")).resolves.toEqual(stats);
    await expect(
      getAdminFeedbackStats({ range: "7d", activityType: "comment", modelConfigId: 5, page: 2, pageSize: 10 })
    ).resolves.toMatchObject({ scope: "global" });

    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/feedback-stats/me?range=30d", {
      credentials: "include",
      headers: { Accept: "application/json" }
    });
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/admin/feedback-stats?range=7d&activityType=comment&modelConfigId=5&page=2&pageSize=10",
      {
        credentials: "include",
        headers: { Accept: "application/json" }
      }
    );
  });

  test("评分配置词表列表传递筛选和分页参数", async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockJsonResponse({ items: [], total: 0, page: 2, pageSize: 10 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      listRuleTerms({
        page: 2,
        pageSize: 10,
        keyword: "安全",
        dictionaryType: "dangerous_pattern",
        enabled: true
      })
    ).resolves.toMatchObject({ total: 0 });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/admin/scoring/rule-terms?page=2&pageSize=10&keyword=%E5%AE%89%E5%85%A8&dictionaryType=dangerous_pattern&enabled=true",
      {
        credentials: "include",
        headers: { Accept: "application/json" }
      }
    );
  });
});
