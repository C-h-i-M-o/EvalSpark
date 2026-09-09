import { Alert, Button, Card, Form, Input, InputNumber, Space, Switch } from "antd";
import { useEmbeddingConfig } from "../features/embedding/useEmbeddingConfig";

export function EmbeddingConfigPage() {
  const state = useEmbeddingConfig();
  return <section className="admin-page settings-page">
    <header className="page-head">
      <div><p className="eyebrow">模型设置</p><h2>Embedding 配置</h2><p>统一管理知识库索引与 RAG 检索使用的向量模型接口。</p></div>
    </header>
    <Card className="admin-table-panel" title="Embedding 服务" loading={!state.config && !state.error}>
    <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
      <Alert type="info" showIcon title="全系统统一配置" description="云端与本地均通过 OpenAI-compatible 接口连接。文档块与检索查询将发送到这里配置的服务。Base URL 应包含供应商要求的版本前缀，例如 /v1。" />
      {state.error && <Alert type="error" showIcon title={state.error} />}
      {state.notice && <Alert type="success" showIcon title={state.notice} />}
      <Form form={state.form} layout="vertical" disabled={state.busy || !state.config}>
        <Form.Item name="baseUrl" label="Base URL" rules={[{ required: true, type: "url", message: "请输入完整的 HTTP 或 HTTPS 地址" }]}><Input placeholder="http://host.docker.internal:8080/v1" /></Form.Item>
        <Form.Item name="apiKey" label="API Key" extra={state.config?.hasApiKey ? "已配置，留空保留原密钥。" : "无鉴权的本地服务可以留空。"}><Input.Password autoComplete="new-password" /></Form.Item>
        <Form.Item name="clearApiKey" label="清除已保存密钥" valuePropName="checked"><Switch /></Form.Item>
        <Form.Item name="modelName" label="模型名称" rules={[{ required: true, whitespace: true }]}><Input /></Form.Item>
        <Form.Item name="dimensions" label="输出向量维度" rules={[{ required: true }]}><InputNumber min={1} max={65536} precision={0} /></Form.Item>
        <Form.Item name="queryPrefix" label="查询前缀" extra="仅在模型要求时填写；文档内容不会添加该前缀。"><Input.TextArea rows={2} maxLength={2000} /></Form.Item>
        <Form.Item name="timeoutSeconds" label="超时（秒）" rules={[{ required: true }]}><InputNumber min={1} max={300} precision={0} /></Form.Item>
        <Form.Item name="batchSize" label="单批最大文本数" rules={[{ required: true }]}><InputNumber min={1} max={16} precision={0} /></Form.Item>
        <Form.Item name="maxInputCharacters" label="单批字符上限" extra="用于限制请求长度，不代表模型 Token 数；供应商仍可能有更严格的限制。" rules={[{ required: true }]}><InputNumber min={2048} max={32768} precision={0} /></Form.Item>
        <Form.Item name="enabled" label="启用服务" valuePropName="checked"><Switch /></Form.Item>
        <Space><Button onClick={state.test} loading={state.busy}>测试连接</Button><Button type="primary" onClick={state.save} loading={state.busy}>保存配置</Button></Space>
      </Form>
    </Space>
    </Card>
  </section>;
}
