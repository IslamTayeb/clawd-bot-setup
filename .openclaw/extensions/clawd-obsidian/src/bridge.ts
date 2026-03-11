import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

export type BridgeEnvelope =
  | {
      ok: true;
      command: string;
      result: unknown;
      meta?: Record<string, unknown>;
    }
  | {
      ok: false;
      command: string;
      error: {
        type?: string;
        message: string;
      };
    };

export type BridgeSuccessEnvelope = Extract<BridgeEnvelope, { ok: true }>;

export type BridgeConfig = {
  pythonExec: string;
  bridgeCwd: string;
  timeoutMs: number;
};

function defaultPythonFor(cwd: string): string {
  const venvPython = path.join(cwd, ".venv", "bin", "python");
  if (fs.existsSync(venvPython)) {
    return venvPython;
  }
  return "python3";
}

export function resolveBridgeConfig(pluginConfig: Record<string, unknown> | undefined): BridgeConfig {
  const bridgeCwd =
    typeof pluginConfig?.bridgeCwd === "string" && pluginConfig.bridgeCwd.trim()
      ? path.resolve(process.cwd(), pluginConfig.bridgeCwd)
      : process.cwd();
  const pythonExec =
    typeof pluginConfig?.pythonExec === "string" && pluginConfig.pythonExec.trim()
      ? pluginConfig.pythonExec
      : defaultPythonFor(bridgeCwd);
  const timeoutMs =
    typeof pluginConfig?.timeoutMs === "number" && Number.isFinite(pluginConfig.timeoutMs)
      ? Math.max(1000, pluginConfig.timeoutMs)
      : 60_000;

  return {
    pythonExec,
    bridgeCwd,
    timeoutMs,
  };
}

function parseEnvelope(stdout: string): BridgeEnvelope {
  const trimmed = stdout.trim();
  if (!trimmed) {
    throw new Error("bridge returned no output");
  }
  return JSON.parse(trimmed) as BridgeEnvelope;
}

export async function runBridgeCommand(
  config: BridgeConfig,
  command: string,
  payload: Record<string, unknown>,
): Promise<BridgeSuccessEnvelope> {
  return await new Promise<BridgeSuccessEnvelope>((resolve, reject) => {
    const child = spawn(config.pythonExec, ["-m", "clawd_ops", command, "--json"], {
      cwd: config.bridgeCwd,
      env: process.env,
      stdio: ["pipe", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    let settled = false;

    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      finish(new Error(`bridge command timed out after ${config.timeoutMs}ms`));
    }, config.timeoutMs);

    const finish = (value: BridgeSuccessEnvelope | Error) => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      if (value instanceof Error) {
        reject(value);
      } else {
        resolve(value);
      }
    };

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");

    child.stdout.on("data", (chunk) => {
      stdout += String(chunk);
    });
    child.stderr.on("data", (chunk) => {
      stderr += String(chunk);
    });

    child.on("error", (error) => {
      finish(error);
    });

    child.on("exit", (code) => {
      try {
        const envelope = parseEnvelope(stdout);
        if (code !== 0 || !envelope.ok) {
          const message =
            !envelope.ok && envelope.error?.message
              ? envelope.error.message
              : stderr.trim() || `bridge command failed (${code ?? "?"})`;
          finish(new Error(message));
          return;
        }
        finish(envelope);
      } catch (error) {
        const message = stderr.trim() || stdout.trim() || `bridge command failed (${code ?? "?"})`;
        finish(error instanceof Error ? new Error(`${error.message}: ${message}`) : new Error(message));
      }
    });

    child.stdin.end(JSON.stringify(payload));
  });
}

export function bridgeResultToToolResult(result: unknown): {
  content: Array<{ type: "text"; text: string }>;
  details: unknown;
} {
  const text = typeof result === "string" ? result : JSON.stringify(result, null, 2);
  return {
    content: [{ type: "text", text }],
    details: result,
  };
}
