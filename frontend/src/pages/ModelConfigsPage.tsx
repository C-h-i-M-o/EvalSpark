import { useEffect, useMemo, useState } from "react";
import {
  Button,
  Card,
  Collapse,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tabs,
  message
} from "antd";
import type { TableColumnsType } from "antd";

import {
  ApiError,
  createModelConfig,
  deleteModelConfig,
  listModelConfigs,
  testModelConfig,
  updateModelConfig
} from "../api/client";
import type { ModelConfig, ModelConfigPayload } from "../api/client";
import { EmbeddingConfigPage } from "./EmbeddingConfigPage";
import { useModelSettingsTabs } from "../features/embedding/useModelSettingsTabs";

const providerPresets = [
  { key: "deepseek", label: "DeepSeek", providerName: "deepseek", baseUrl: "https://api.deepseek.com", description: "DeepSeek 官方兼容接口" },
  { key: "minimax", label: "MiniMax", providerName: "minimax", baseUrl: "https://api.minimaxi.com/v1", description: "MiniMax OpenAI-compatible 接口" },
  { key: "glm", label: "智谱 GLM", providerName: "glm", baseUrl: "https://open.bigmodel.cn/api/paas/v4", description: "智谱 GLM 兼容接口" },
  { key: "qwen", label: "阿里 Qwen", providerName: "qwen", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", description: "DashScope 兼容模式" },
  { key: "openai", label: "OpenAI", providerName: "openai", baseUrl: "https://api.openai.com/v1", description: "OpenAI 官方接口" },
  { key: "custom", label: "OpenAI-compatible", providerName: "", baseUrl: "", description: "自定义兼容供应商" }
];

interface ModelConfigFormValues extends Required<ModelConfigPayload> {}

const emptyForm: ModelConfigFormValues = {
  providerName: "",
  displayName: "",
  modelName: "",
  baseUrl: "",
  apiKey: "",
  enabled: true,
  maxTokens: 1024,
  temperature: 0.7,
  timeoutSeconds: 60,
  notes: "",
  currency: "CNY",
  priceInput: 0,
  priceOutput: 0,
  priceCacheHit: 0,
  priceCacheCreation: 0
};

export function ModelConfigsPage() {
  const tabs = useModelSettingsTabs();
  return (
    <section className="admin-page settings-page">
      <Tabs
        activeKey={tabs.activeKey}
        onChange={tabs.changeTab}
        items={[
          { key: "models", label: "大模型配置", children: <ModelProvidersPanel /> },
          { key: "embedding", label: "Embedding 配置", children: <EmbeddingConfigPage /> }
        ]}
      />
    </section>
  );
}

function ModelProvidersPanel() {
  const [form] = Form.useForm<ModelConfigFormValues>();
  const [configs, setConfigs] = useState<ModelConfig[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testingId, setTestingId] = useState<number | "draft" | null>(null);
  const [editingConfig, setEditingConfig] = useState<ModelConfig | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [selectedPresetKey, setSelectedPresetKey] = useState("deepseek");

  useEffect(() => {
    void loadConfigs();
  }, []);

  const columns = useMemo<TableColumnsType<ModelConfig>>(
    () => [
      { title: "供应商", dataIndex: "providerName", width: 130 },
      { title: "展示名", dataIndex: "displayName", width: 160 },
      { title: "模型名", dataIndex: "modelName", width: 180 },
      { title: "Base URL", dataIndex: "baseUrl", ellipsis: true },
      { title: "币种", dataIndex: "currency", width: 84 },
      {
        title: "密钥",
        width: 130,
        render: (_, row) => <Tag color={row.hasApiKey ? "green" : "orange"}>{row.hasApiKey ? row.maskedApiKey : "未配置"}</Tag>
      },
      {
        title: "启用",
        width: 90,
        render: (_, row) => <Switch checked={row.enabled} onChange={() => void toggleEnabled(row)} />
      },
      {
        title: "操作",
        width: 230,
        fixed: "right",
        render: (_, row) => (
          <Space>
            <Button size="small" onClick={() => openEditModal(row)}>编辑</Button>
            <Button size="small" loading={testingId === row.id} onClick={() => void testSavedConfig(row)}>测试</Button>
            <Button size="small" danger onClick={() => confirmDelete(row)}>删除</Button>
          </Space>
        )
      }
    ],
    [testingId]
  );

  async function loadConfigs(): Promise<void> {
    setLoading(true);
    try {
      setConfigs(await listModelConfigs());
    } catch (error) {
      message.error(getErrorMessage(error, "模型配置加载失败"));
    } finally {
      setLoading(false);
    }
  }

  function openCreateModal(): void {
    const firstPreset = providerPresets[0];
    setEditingConfig(null);
    setSelectedPresetKey(firstPreset.key);
    form.setFieldsValue({ ...emptyForm, providerName: firstPreset.providerName, baseUrl: firstPreset.baseUrl });
    setModalOpen(true);
  }

  function openEditModal(config: ModelConfig): void {
    const preset = providerPresets.find((item) => item.providerName === config.providerName);
    setEditingConfig(config);
    setSelectedPresetKey(preset?.key ?? "custom");
    form.setFieldsValue({
      providerName: config.providerName,
      displayName: config.displayName,
      modelName: config.modelName,
      baseUrl: config.baseUrl,
      apiKey: "",
      enabled: config.enabled,
      maxTokens: config.maxTokens,
      temperature: config.temperature,
      timeoutSeconds: config.timeoutSeconds,
      notes: config.notes,
      currency: config.currency,
      priceInput: config.priceInput,
      priceOutput: config.priceOutput,
      priceCacheHit: config.priceCacheHit,
      priceCacheCreation: config.priceCacheCreation
    });
    setModalOpen(true);
  }

  function selectPreset(key: string): void {
    const preset = providerPresets.find((item) => item.key === key);
    if (!preset) {
      return;
    }
    setSelectedPresetKey(key);
    form.setFieldsValue({ providerName: preset.providerName, baseUrl: preset.baseUrl });
  }

  async function saveConfig(): Promise<void> {
    const values = await form.validateFields();
    setSaving(true);
    try {
      const payload = buildPayload(values, !editingConfig);
      if (editingConfig) {
        await updateModelConfig(editingConfig.id, payload);
      } else {
        await createModelConfig(payload);
      }
      message.success("模型配置已保存");
      setModalOpen(false);
      await loadConfigs();
    } catch (error) {
      message.error(getErrorMessage(error, "模型配置保存失败"));
    } finally {
      setSaving(false);
    }
  }

  async function toggleEnabled(config: ModelConfig): Promise<void> {
    try {
      await updateModelConfig(config.id, { enabled: !config.enabled });
      await loadConfigs();
    } catch (error) {
      message.error(getErrorMessage(error, "启用状态更新失败"));
    }
  }

  function confirmDelete(config: ModelConfig): void {
    Modal.confirm({
      title: "删除模型配置",
      content: `确认删除 ${config.displayName}？历史回答会保留模型快照。`,
      okText: "删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        await deleteModelConfig(config.id);
        message.success("模型配置已删除");
        await loadConfigs();
      }
    });
  }

  async function testSavedConfig(config: ModelConfig): Promise<void> {
    setTestingId(config.id);
    try {
      const result = await testModelConfig({ modelConfigId: config.id });
      result.success ? message.success(`${result.message}，耗时 ${result.latencyMs}ms`) : message.error(result.message);
    } catch (error) {
      message.error(getErrorMessage(error, "连接测试失败"));
    } finally {
      setTestingId(null);
    }
  }

  async function testDraftConfig(): Promise<void> {
    const values = await form.validateFields();
    setTestingId("draft");
    try {
      const result = await testModelConfig(buildTestPayload(values, editingConfig?.id));
      result.success ? message.success(`${result.message}，耗时 ${result.latencyMs}ms`) : message.error(result.message);
    } catch (error) {
      message.error(getErrorMessage(error, "连接测试失败"));
    } finally {
      setTestingId(null);
    }
  }

  return (
    <section className="admin-page">
      <header className="page-head">
        <div>
          <p className="eyebrow">Model Providers</p>
          <h2>模型配置</h2>
        </div>
        <Space>
          <Button loading={loading} onClick={() => void loadConfigs()}>刷新</Button>
          <Button type="primary" onClick={openCreateModal}>新增配置</Button>
        </Space>
      </header>
      <Card className="admin-table-panel" styles={{ body: { padding: 0 } }}>
        <Table
          rowKey="id"
          columns={columns}
          dataSource={configs}
          loading={loading}
          scroll={{ x: 1120 }}
          pagination={false}
        />
      </Card>
      <Modal
        title={editingConfig ? "编辑模型配置" : "新增模型配置"}
        open={modalOpen}
        width="min(760px, 94vw)"
        okText="保存"
        cancelText="取消"
        confirmLoading={saving}
        onOk={() => void saveConfig()}
        onCancel={() => setModalOpen(false)}
        footer={(_, { OkBtn, CancelBtn }) => (
          <>
            <CancelBtn />
            <Button loading={testingId === "draft"} onClick={() => void testDraftConfig()}>测试连接</Button>
            <OkBtn />
          </>
        )}
      >
        <Form form={form} layout="vertical" initialValues={emptyForm}>
          {!editingConfig ? (
            <section className="mb-4">
              <p className="panel-label">选择供应商</p>
              <div className="provider-preset-grid">
                {providerPresets.map((preset) => (
                  <button
                    key={preset.key}
                    type="button"
                    className={selectedPresetKey === preset.key ? "provider-preset active" : "provider-preset"}
                    onClick={() => selectPreset(preset.key)}
                  >
                    <strong>{preset.label}</strong>
                    <span>{preset.description}</span>
                  </button>
                ))}
              </div>
            </section>
          ) : null}
          <div className="form-grid form-grid-two">
            <Form.Item label="供应商名称" name="providerName" rules={[{ required: true, message: "请填写供应商名称" }]}>
              <Input placeholder="例如 deepseek" />
            </Form.Item>
            <Form.Item label="展示名" name="displayName" rules={[{ required: true, message: "请填写展示名" }]}>
              <Input placeholder="评测页显示的名称" />
            </Form.Item>
          </div>
          <Form.Item label="模型名" name="modelName" rules={[{ required: true, message: "请填写模型名" }]}>
            <Input placeholder="复制官方文档中的 Model ID" />
          </Form.Item>
          <Form.Item label="API Key" name="apiKey">
            <Input.Password placeholder={editingConfig?.hasApiKey ? "留空保留现有密钥" : "粘贴供应商控制台创建的 API Key"} />
          </Form.Item>
          <Form.Item label="启用状态" name="enabled" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Collapse
            ghost
            items={[
              {
                key: "advanced",
                label: "高级选项",
                // 折叠时也注册字段，确保校验和提交包含预设的高级参数。
                forceRender: true,
                children: (
                  <>
                    <Form.Item label="Base URL" name="baseUrl" rules={[{ required: true, message: "请填写 Base URL" }]}>
                      <Input placeholder="例如 https://api.example.com/v1" />
                    </Form.Item>
                    <div className="form-grid form-grid-two">
                      <Form.Item label="温度" name="temperature">
                        <InputNumber min={0} max={2} step={0.1} className="w-full" />
                      </Form.Item>
                      <Form.Item label="最大输出 Token" name="maxTokens">
                        <InputNumber min={1} step={128} className="w-full" />
                      </Form.Item>
                      <Form.Item label="请求超时（秒）" name="timeoutSeconds">
                        <InputNumber min={1} max={600} step={5} className="w-full" />
                      </Form.Item>
                      <Form.Item label="计费币种" name="currency">
                        <Select options={[{ value: "CNY", label: "人民币 CNY" }, { value: "USD", label: "美元 USD" }]} />
                      </Form.Item>
                      <Form.Item label="输入价格" name="priceInput">
                        <InputNumber min={0} step={0.1} className="w-full" />
                      </Form.Item>
                      <Form.Item label="输出价格" name="priceOutput">
                        <InputNumber min={0} step={0.1} className="w-full" />
                      </Form.Item>
                      <Form.Item label="缓存命中价格" name="priceCacheHit">
                        <InputNumber min={0} step={0.1} className="w-full" />
                      </Form.Item>
                      <Form.Item label="缓存创建价格" name="priceCacheCreation">
                        <InputNumber min={0} step={0.1} className="w-full" />
                      </Form.Item>
                    </div>
                    <Form.Item label="管理员备注" name="notes">
                      <Input.TextArea rows={3} maxLength={2000} showCount />
                    </Form.Item>
                  </>
                )
              }
            ]}
          />
        </Form>
      </Modal>
    </section>
  );
}

function buildPayload(form: ModelConfigFormValues, includeEmptyApiKey: boolean): ModelConfigPayload {
  const payload: ModelConfigPayload = { ...form, apiKey: undefined };
  if (includeEmptyApiKey || form.apiKey?.trim()) {
    payload.apiKey = form.apiKey?.trim() || "";
  }
  return payload;
}

function buildTestPayload(form: ModelConfigFormValues, modelConfigId?: number): ModelConfigPayload & { modelConfigId?: number } {
  return { ...buildPayload(form, false), modelConfigId };
}

function getErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError || error instanceof Error) {
    return error.message || fallback;
  }
  return fallback;
}
