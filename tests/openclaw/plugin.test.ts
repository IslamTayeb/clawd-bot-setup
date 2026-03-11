import path from "node:path";
import { describe, expect, it, vi } from "vitest";
import plugin from "../../.openclaw/extensions/clawd-obsidian/index.js";
import {
  bridgeResultToToolResult,
  resolveBridgeConfig,
  runBridgeCommand,
} from "../../.openclaw/extensions/clawd-obsidian/src/bridge.js";

describe("clawd-obsidian plugin", () => {
  it("registers tools and prompt guidance", () => {
    const registerTool = vi.fn();
    const on = vi.fn();

    plugin.register({
      id: "clawd-obsidian",
      name: "Clawd Obsidian",
      source: path.join(process.cwd(), ".openclaw/extensions/clawd-obsidian/index.ts"),
      config: {},
      runtime: {} as never,
      logger: {} as never,
      resolvePath: (input: string) => path.resolve(process.cwd(), input),
      registerTool,
      registerHook: vi.fn(),
      registerHttpRoute: vi.fn(),
      registerChannel: vi.fn(),
      registerGatewayMethod: vi.fn(),
      registerCli: vi.fn(),
      registerService: vi.fn(),
      registerProvider: vi.fn(),
      registerCommand: vi.fn(),
      registerContextEngine: vi.fn(),
      on,
    });

    expect(registerTool).toHaveBeenCalledTimes(11);
    expect(on).toHaveBeenCalledWith("before_prompt_build", expect.any(Function));
  });

  it("resolves the default bridge config", () => {
    const config = resolveBridgeConfig(undefined);
    expect(config.bridgeCwd).toBe(process.cwd());
    expect(config.timeoutMs).toBe(60000);
    expect(config.pythonExec.length).toBeGreaterThan(0);
  });

  it("runs the python bridge command", async () => {
    const config = resolveBridgeConfig({
      bridgeCwd: ".",
      pythonExec: process.env.OPENCLAW_PYTHON ?? ".venv/bin/python",
      timeoutMs: 10000,
    });
    const envelope = await runBridgeCommand(config, "task_file_path", { target_date: "today" });
    expect(envelope.ok).toBe(true);
    if (envelope.ok) {
      expect(String(envelope.result)).toMatch(/^tasks\/\d{6}\.md$/);
    }
  });

  it("formats bridge results for tool output", () => {
    expect(bridgeResultToToolResult("hello")).toEqual({
      content: [{ type: "text", text: "hello" }],
      details: "hello",
    });
  });
});
