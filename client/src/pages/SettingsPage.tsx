import { useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  type ConnectionTestResult,
  type ManagedModel,
  type PlatformSettings,
} from "../service/http";

const EMPTY_SETTINGS: PlatformSettings = {
  server: "",
  tokens: { tournament: "", match: "", test_room: [] },
  selected_model: null,
};

function messageOf(error: unknown, fallback: string): string {
  const message = error instanceof Error ? error.message : String(error ?? "");
  return message || fallback;
}

function fileToBase64(file: File): Promise<string> {
  return file.arrayBuffer().then((buffer) => {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    const chunk = 0x8000;
    for (let start = 0; start < bytes.length; start += chunk) {
      binary += String.fromCharCode(...bytes.subarray(start, start + chunk));
    }
    return btoa(binary);
  });
}

function formatSize(size: number): string {
  if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))} KiB`;
  return `${(size / 1024 / 1024).toFixed(1)} MiB`;
}

export function SettingsPage() {
  const [settings, setSettings] = useState<PlatformSettings>(EMPTY_SETTINGS);
  const [models, setModels] = useState<ManagedModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [testing, setTesting] = useState<ConnectionTestResult["mode"] | null>(null);
  const [testMessages, setTestMessages] = useState<Partial<Record<ConnectionTestResult["mode"], string>>>({});
  const [modelFile, setModelFile] = useState<File | null>(null);
  const [modelBusy, setModelBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    Promise.all([api.getSettings(), api.listModels()])
      .then(([loadedSettings, loadedModels]) => {
        if (!active) return;
        setSettings({
          ...EMPTY_SETTINGS,
          ...loadedSettings,
          tokens: {
            ...EMPTY_SETTINGS.tokens,
            ...(loadedSettings.tokens ?? {}),
            test_room: [...(loadedSettings.tokens?.test_room ?? [])],
          },
        });
        setModels(loadedModels.models ?? []);
      })
      .catch((reason) => {
        if (active) setError(messageOf(reason, "读取设置失败"));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const selectedModel = useMemo(
    () => models.find((model) => model.selected)?.name ?? settings.selected_model,
    [models, settings.selected_model],
  );

  function updateToken(kind: "tournament" | "match", value: string) {
    setSettings((current) => ({
      ...current,
      tokens: { ...current.tokens, [kind]: value },
    }));
  }

  function updateTestToken(index: number, value: string) {
    setSettings((current) => {
      const testRoom = [...current.tokens.test_room];
      testRoom[index] = value;
      return { ...current, tokens: { ...current.tokens, test_room: testRoom } };
    });
  }

  function addTestToken() {
    setSettings((current) => ({
      ...current,
      tokens: { ...current.tokens, test_room: [...current.tokens.test_room, ""] },
    }));
  }

  function removeTestToken(index: number) {
    setSettings((current) => ({
      ...current,
      tokens: {
        ...current.tokens,
        test_room: current.tokens.test_room.filter((_, item) => item !== index),
      },
    }));
  }

  async function saveSettings() {
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const saved = await api.saveSettings({
        server: settings.server,
        tokens: settings.tokens,
      });
      setSettings((current) => ({ ...current, ...saved }));
      setMessage("平台连接设置已保存，下一次会话立即生效");
    } catch (reason) {
      setError(messageOf(reason, "保存设置失败"));
    } finally {
      setSaving(false);
    }
  }

  async function testConnection(mode: ConnectionTestResult["mode"]) {
    const token = mode === "test_room"
      ? settings.tokens.test_room[0] ?? ""
      : settings.tokens[mode];
    setTesting(mode);
    setError(null);
    try {
      const result = await api.testConnection({
        mode,
        server: settings.server,
        token,
      });
      setTestMessages((current) => ({ ...current, [mode]: result.message }));
    } catch (reason) {
      setTestMessages((current) => ({
        ...current,
        [mode]: messageOf(reason, "连接测试失败"),
      }));
    } finally {
      setTesting(null);
    }
  }

  async function importModel() {
    if (!modelFile) {
      setError("请先选择 .onnx 文件");
      return;
    }
    setModelBusy(true);
    setError(null);
    setMessage(null);
    try {
      const imported = await api.importModel(modelFile.name, await fileToBase64(modelFile));
      setModels((current) => [...current, imported]);
      setModelFile(null);
      if (fileInput.current) fileInput.current.value = "";
      setMessage(`模型 ${imported.name} 已导入并通过契约校验`);
    } catch (reason) {
      // 不更新 models 或 selected_model：坏模型不能影响当前选择。
      setError(`模型导入失败：${messageOf(reason, "文件无效")}`);
    } finally {
      setModelBusy(false);
    }
  }

  async function selectModel(name: string) {
    setModelBusy(true);
    setError(null);
    setMessage(null);
    try {
      const selected = await api.selectModel(name);
      setModels((current) => current.map((model) => ({
        ...model,
        selected: model.name === selected.name,
      })));
      setSettings((current) => ({ ...current, selected_model: selected.name }));
      setMessage(`已选择 ${selected.name}，仅影响之后启动的新会话`);
    } catch (reason) {
      setError(`模型切换失败：${messageOf(reason, "模型不可用")}`);
    } finally {
      setModelBusy(false);
    }
  }

  async function deleteModel(name: string) {
    setModelBusy(true);
    setError(null);
    try {
      await api.deleteModel(name);
      setModels((current) => current.filter((model) => model.name !== name));
      setMessage(`模型 ${name} 已删除`);
    } catch (reason) {
      setError(`模型删除失败：${messageOf(reason, "无法删除模型")}`);
    } finally {
      setModelBusy(false);
    }
  }

  if (loading) {
    return <section className="page settings-page" data-page="settings"><p>正在读取设置…</p></section>;
  }

  return (
    <section className="page settings-page" data-page="settings">
      <div className="settings-header">
        <div>
          <p className="section-kicker">CLIENT SETTINGS</p>
          <h1>设置</h1>
          <p className="section-description">平台连接与本地 ONNX 推理模型。</p>
        </div>
      </div>

      {message && <p className="settings-notice" role="status">{message}</p>}
      {error && <p className="error settings-error" role="alert">{error}</p>}

      <div className="settings-grid">
        <section className="settings-card" aria-labelledby="platform-settings-title">
          <div className="settings-card-header">
            <div>
              <h2 id="platform-settings-title">平台连接</h2>
              <p className="muted">令牌仅保存到本机 gitignored 的 local/platform.json。</p>
            </div>
          </div>
          <label className="settings-field">
            服务器地址
            <input
              aria-label="服务器地址"
              value={settings.server}
              placeholder="https://mahjong.example.com"
              onChange={(event) => setSettings((current) => ({ ...current, server: event.target.value }))}
            />
          </label>
          <div className="settings-token-row">
            <label className="settings-field">
              锦标赛令牌
              <input
                type="password"
                autoComplete="off"
                aria-label="锦标赛令牌"
                value={settings.tokens.tournament}
                onChange={(event) => updateToken("tournament", event.target.value)}
              />
            </label>
            <button className="secondary-button settings-test" onClick={() => testConnection("tournament")} disabled={testing !== null}>
              {testing === "tournament" ? "测试中…" : "测试锦标赛连接"}
            </button>
          </div>
          {testMessages.tournament && <p className="settings-test-message">{testMessages.tournament}</p>}

          <div className="settings-token-row">
            <label className="settings-field">
              匹配令牌
              <input
                type="password"
                autoComplete="off"
                aria-label="匹配令牌"
                value={settings.tokens.match}
                onChange={(event) => updateToken("match", event.target.value)}
              />
            </label>
            <button className="secondary-button settings-test" onClick={() => testConnection("match")} disabled={testing !== null}>
              {testing === "match" ? "测试中…" : "测试匹配房连接"}
            </button>
          </div>
          {testMessages.match && <p className="settings-test-message">{testMessages.match}</p>}

          <div className="settings-field">
            <div className="settings-label-row">
              <span>测试房令牌</span>
              <button type="button" className="text-button" onClick={addTestToken}>+ 添加令牌</button>
            </div>
            <div className="test-token-list">
              {settings.tokens.test_room.map((token, index) => (
                <div className="test-token-item" key={index}>
                  <input
                    type="password"
                    autoComplete="off"
                    aria-label={`测试房令牌 ${index + 1}`}
                    value={token}
                    onChange={(event) => updateTestToken(index, event.target.value)}
                  />
                  <button type="button" className="icon-button" aria-label={`移除测试房令牌 ${index + 1}`} onClick={() => removeTestToken(index)}>移除</button>
                </div>
              ))}
              {settings.tokens.test_room.length === 0 && <p className="muted settings-empty-token">尚未添加测试房令牌。</p>}
            </div>
            <button className="secondary-button settings-test" onClick={() => testConnection("test_room")} disabled={testing !== null || settings.tokens.test_room.length === 0}>
              {testing === "test_room" ? "测试中…" : "测试测试房连接"}
            </button>
          </div>
          {testMessages.test_room && <p className="settings-test-message">{testMessages.test_room}</p>}

          <button className="primary-button settings-save" onClick={saveSettings} disabled={saving}>
            {saving ? "保存中…" : "保存平台设置"}
          </button>
        </section>

        <section className="settings-card" aria-labelledby="model-settings-title">
          <div className="settings-card-header">
            <div>
              <h2 id="model-settings-title">模型管理</h2>
              <p className="muted">导入带有输入契约元数据的 policy / policy-v3 ONNX 文件。</p>
            </div>
            {selectedModel && <span className="model-selected-badge">当前：{selectedModel}</span>}
          </div>
          <div className="model-import-row">
            <input
              ref={fileInput}
              type="file"
              accept=".onnx,application/onnx"
              aria-label="导入 ONNX 模型"
              onChange={(event) => setModelFile(event.target.files?.[0] ?? null)}
            />
            <button className="primary-button" onClick={importModel} disabled={modelBusy || !modelFile}>
              {modelBusy ? "处理中…" : "导入模型"}
            </button>
          </div>
          <p className="muted model-hint">导入失败只提示原因，不会改变当前已选模型；切换仅对之后的新会话生效。</p>
          <div className="model-list">
            {models.length === 0 && <p className="empty-state">暂无已导入模型。</p>}
            {models.map((model) => (
              <div className={`model-item${model.selected ? " model-item-selected" : ""}`} key={model.name}>
                <div className="model-main">
                  <strong>{model.name}</strong>
                  <span>{formatSize(model.size)} · {model.contract.planes ?? "?"} planes · {model.contract.actions ?? "?"} actions</span>
                </div>
                <div className="model-actions">
                  {model.selected
                    ? <span className="model-current">已选择</span>
                    : <button className="secondary-button" onClick={() => selectModel(model.name)} disabled={modelBusy}>选择</button>}
                  <button className="text-button danger-button" onClick={() => deleteModel(model.name)} disabled={modelBusy || model.selected}>删除</button>
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>

      <div className="settings-mode-note">
        连接测试只访问平台身份接口，不会创建房间或启动对局。不同模式的令牌不能混用。
      </div>
    </section>
  );
}
