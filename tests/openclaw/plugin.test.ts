import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
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

    expect(registerTool).toHaveBeenCalledTimes(14);
    expect(registerTool).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "list_conflicts",
      }),
    );
    expect(registerTool).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "resolve_conflict",
      }),
    );
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

  it("renders a valid OpenClaw runtime config", () => {
    const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "clawd-openclaw-"));
    const configPath = path.join(tempDir, "openclaw.runtime.json");
    const pythonExec = process.env.OPENCLAW_PYTHON ?? path.resolve(".venv/bin/python");
    const openclawEntrypoint = path.resolve("node_modules/openclaw/openclaw.mjs");

    execFileSync(
      pythonExec,
      [
        "-m",
        "clawd_ops.openclaw_config",
        "--output",
        configPath,
        "--workspace",
        process.cwd(),
        "--python-exec",
        pythonExec,
      ],
      {
        cwd: process.cwd(),
        env: {
          ...process.env,
          TELEGRAM_TOKEN: "123456:abc",
          ALLOWED_USER_ID: "8383879897",
          AWS_REGION: "us-east-1",
          BEDROCK_MODEL_ID: "us.anthropic.claude-opus-4-6-v1",
          BOT_TIMEZONE: "America/New_York",
        },
      },
    );

    const validation = spawnSync(
      process.execPath,
      [openclawEntrypoint, "config", "validate", "--json"],
      {
        cwd: process.cwd(),
        env: {
          ...process.env,
          OPENCLAW_CONFIG_PATH: configPath,
        },
        encoding: "utf8",
      },
    );

    expect(validation.status).toBe(0);
    if (validation.stdout.trim()) {
      expect(JSON.parse(validation.stdout)).toEqual(
        expect.objectContaining({
          valid: true,
        }),
      );
    }
  }, 15000);
});
