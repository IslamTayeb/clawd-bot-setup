import { Type } from "@sinclair/typebox";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";
import { bridgeResultToToolResult, resolveBridgeConfig, runBridgeCommand } from "./src/bridge.js";
import { CLAWD_OBSIDIAN_GUIDANCE } from "./src/prompt-guidance.js";

type IdeaRecord = {
  id: string;
  idea_path: string;
  report_path: string;
};

type PluginCommandDefinition = Parameters<OpenClawPluginApi["registerCommand"]>[0];
type IdeaCommandContext = Parameters<PluginCommandDefinition["handler"]>[0];

const pluginConfigSchema = Type.Object(
  {
    pythonExec: Type.Optional(Type.String()),
    bridgeCwd: Type.Optional(Type.String()),
    timeoutMs: Type.Optional(Type.Integer({ minimum: 1000 })),
  },
  { additionalProperties: false },
);

function createBridgeTool(params: {
  command: string;
  name: string;
  label: string;
  description: string;
  parameters: object;
  api: OpenClawPluginApi;
}) {
  return {
    name: params.name,
    label: params.label,
    description: params.description,
    parameters: params.parameters,
    async execute(_toolCallId: string, payload: Record<string, unknown>) {
      const config = resolveBridgeConfig(params.api.pluginConfig);
      const envelope = await runBridgeCommand(config, params.command, payload);
      return bridgeResultToToolResult(envelope.result);
    },
  };
}

const NO_PARAMS = Type.Object({}, { additionalProperties: false });

const IDEA_RESEARCH_TIMEOUT_MS = 30 * 60 * 1000;

function parseIdeaRecord(result: unknown): IdeaRecord {
  if (!result || typeof result !== "object") {
    throw new Error("bridge returned an invalid idea record");
  }
  const record = result as Partial<IdeaRecord>;
  if (
    typeof record.id !== "string" ||
    typeof record.idea_path !== "string" ||
    typeof record.report_path !== "string"
  ) {
    throw new Error("bridge returned an incomplete idea record");
  }
  return {
    id: record.id,
    idea_path: record.idea_path,
    report_path: record.report_path,
  };
}

function resolveTelegramDmRecipient(ctx: IdeaCommandContext): string | null {
  if (ctx.channel !== "telegram") {
    return null;
  }
  const senderId = ctx.senderId?.trim();
  if (senderId) {
    return senderId;
  }
  const from = ctx.from?.trim();
  if (from?.startsWith("telegram:")) {
    return from.slice("telegram:".length);
  }
  return from || null;
}

async function sendTelegramDm(api: OpenClawPluginApi, ctx: IdeaCommandContext, text: string) {
  const recipient = resolveTelegramDmRecipient(ctx);
  if (!recipient) {
    api.logger.warn("idea research completion has no Telegram DM recipient");
    return;
  }
  await api.runtime.channel.telegram.sendMessageTelegram(recipient, text, {
    cfg: ctx.config,
    accountId: ctx.accountId,
  });
}

function textFromUnknown(value: unknown, depth = 0): string {
  if (depth > 4 || value == null) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  if (Array.isArray(value)) {
    return value.map((item) => textFromUnknown(item, depth + 1)).filter(Boolean).join("\n");
  }
  if (typeof value === "object") {
    const objectValue = value as Record<string, unknown>;
    for (const key of ["text", "plainText", "body", "content", "message", "output", "result"]) {
      const text = textFromUnknown(objectValue[key], depth + 1).trim();
      if (text) {
        return text;
      }
    }
  }
  return "";
}

function latestMessageText(messages: unknown[]): string {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const text = textFromUnknown(messages[index]).trim();
    if (text) {
      return text;
    }
  }
  return "";
}

function ideaResearchPrompt(idea: string, record: IdeaRecord): string {
  return `
Research this saved world-breaking idea.

Idea:
${idea}

Full report path:
${record.report_path}

Use the available research tools: search_web, search_papers, search_github_repos, browse_web, read_notes, and write_note. Save the full markdown report to the exact report path above with write_note in overwrite mode.

The report must include:
- Interpretation of the idea
- Closest existing products, papers, and repositories
- What is genuinely new or missing
- Concise next-step takeaways

When finished, reply only with a concise Telegram-safe completion summary and the report path. Do not include the full report in the chat reply.
`.trim();
}

function ideaResearchSystemPrompt(): string {
  return `
You are a background research agent for Clawd's Obsidian vault. Be factual, cite URLs in the saved report, and keep the final chat reply short. If research fails, explain the failure briefly and do not alter the saved idea entry.
`.trim();
}

async function runIdeaResearch(api: OpenClawPluginApi, ctx: IdeaCommandContext, idea: string, record: IdeaRecord) {
  try {
    const sessionKey = api.runtime.channel.routing.buildAgentSessionKey({
      agentId: "main",
      channel: "plugin",
      peer: { kind: "direct", id: `world-breaking-idea-${record.id}` },
    });
    const run = await api.runtime.subagent.run({
      sessionKey,
      message: ideaResearchPrompt(idea, record),
      extraSystemPrompt: ideaResearchSystemPrompt(),
      lane: "background",
      deliver: false,
      idempotencyKey: `world-breaking-idea:${record.id}`,
    });
    const wait = await api.runtime.subagent.waitForRun({
      runId: run.runId,
      timeoutMs: IDEA_RESEARCH_TIMEOUT_MS,
    });
    if (wait.status !== "ok") {
      const detail = wait.error ? ` ${wait.error}` : "";
      await sendTelegramDm(
        api,
        ctx,
        `Idea saved, but the research run failed.${detail}\nReport path: ${record.report_path}`,
      );
      return;
    }

    const transcript = await api.runtime.subagent.getSessionMessages({ sessionKey, limit: 20 });
    const summary =
      latestMessageText(transcript.messages) || `Research complete.\nReport: ${record.report_path}`;
    await sendTelegramDm(api, ctx, summary);
  } catch (error) {
    api.logger.error(
      `idea research background run failed: ${error instanceof Error ? error.message : String(error)}`,
    );
    try {
      await sendTelegramDm(
        api,
        ctx,
        `Idea saved, but the research run failed.\nReport path: ${record.report_path}`,
      );
    } catch (notifyError) {
      api.logger.error(
        `failed to send idea research failure DM: ${
          notifyError instanceof Error ? notifyError.message : String(notifyError)
        }`,
      );
    }
  }
}

function createIdeaCommand(api: OpenClawPluginApi) {
  return {
    name: "idea",
    description: "Save a world-breaking idea and start background research.",
    acceptsArgs: true,
    requireAuth: true,
    handler: async (ctx: IdeaCommandContext) => {
      const idea = ctx.args?.trim() ?? "";
      if (!idea) {
        return { text: "Usage: /idea <idea>" };
      }

      const config = resolveBridgeConfig(api.pluginConfig);
      const envelope = await runBridgeCommand(config, "add_world_breaking_idea", { idea });
      const record = parseIdeaRecord(envelope.result);
      void runIdeaResearch(api, ctx, idea, record);

      return {
        text:
          `Saved idea to ${record.idea_path}.\n` +
          `Research started. I'll DM the summary when it finishes.\n` +
          `Report: ${record.report_path}`,
      };
    },
  };
}

const toolDefinitions = [
  {
    command: "add_todos",
    name: "add_todos",
    label: "Add Todos",
    description: "Add todo items to the dated Obsidian task note.",
    parameters: Type.Object(
      {
        items: Type.Array(Type.String()),
        target_date: Type.Optional(Type.String()),
      },
      { additionalProperties: false },
    ),
  },
  {
    command: "add_world_breaking_idea",
    name: "add_world_breaking_idea",
    label: "Add World-Breaking Idea",
    description: "Append a world-breaking idea to the vault and reserve a report path.",
    parameters: Type.Object(
      {
        idea: Type.String(),
        report_path: Type.Optional(Type.String()),
      },
      { additionalProperties: false },
    ),
  },
  {
    command: "read_task_list",
    name: "read_task_list",
    label: "Read Task List",
    description: "Read a dated Obsidian task note.",
    parameters: Type.Object(
      {
        target_date: Type.Optional(Type.String()),
      },
      { additionalProperties: false },
    ),
  },
  {
    command: "read_notes",
    name: "read_notes",
    label: "Read Notes",
    description: "Read a markdown note from the vault.",
    parameters: Type.Object(
      {
        path: Type.String(),
      },
      { additionalProperties: false },
    ),
  },
  {
    command: "write_note",
    name: "write_note",
    label: "Write Note",
    description: "Create or update a markdown file in the vault.",
    parameters: Type.Object(
      {
        path: Type.String(),
        content: Type.String(),
        mode: Type.Optional(Type.Union([Type.Literal("overwrite"), Type.Literal("append"), Type.Literal("prepend")])),
      },
      { additionalProperties: false },
    ),
  },
  {
    command: "list_files",
    name: "list_files",
    label: "List Files",
    description: "List markdown files under a vault folder.",
    parameters: Type.Object(
      {
        folder: Type.Optional(Type.String()),
      },
      { additionalProperties: false },
    ),
  },
  {
    command: "save_research",
    name: "save_research",
    label: "Save Research",
    description: "Save a research summary to the vault.",
    parameters: Type.Object(
      {
        title: Type.String(),
        content: Type.String(),
      },
      { additionalProperties: false },
    ),
  },
  {
    command: "search_papers",
    name: "search_papers",
    label: "Search Papers",
    description: "Search arXiv and Google Scholar.",
    parameters: Type.Object(
      {
        query: Type.String(),
        max_results: Type.Optional(Type.Integer({ minimum: 1, maximum: 10 })),
      },
      { additionalProperties: false },
    ),
  },
  {
    command: "search_web",
    name: "search_web",
    label: "Search Web",
    description: "Search the web for research sources.",
    parameters: Type.Object(
      {
        query: Type.String(),
        max_results: Type.Optional(Type.Integer({ minimum: 1, maximum: 20 })),
      },
      { additionalProperties: false },
    ),
  },
  {
    command: "search_github_repos",
    name: "search_github_repos",
    label: "Search GitHub Repos",
    description: "Search GitHub repositories relevant to a research query.",
    parameters: Type.Object(
      {
        query: Type.String(),
        max_results: Type.Optional(Type.Integer({ minimum: 1, maximum: 20 })),
      },
      { additionalProperties: false },
    ),
  },
  {
    command: "browse_web",
    name: "browse_web",
    label: "Browse Web",
    description: "Fetch readable text from a URL.",
    parameters: Type.Object(
      {
        url: Type.String(),
      },
      { additionalProperties: false },
    ),
  },
  {
    command: "read_memory",
    name: "read_memory",
    label: "Read Memory",
    description: "Read Clawd's persistent memory file.",
    parameters: NO_PARAMS,
  },
  {
    command: "remember_memory",
    name: "remember_memory",
    label: "Remember Memory",
    description: "Store a durable preference or long-lived fact in Clawd memory.",
    parameters: Type.Object(
      {
        memory: Type.String(),
        section: Type.Optional(Type.String()),
      },
      { additionalProperties: false },
    ),
  },
  {
    command: "forget_memory",
    name: "forget_memory",
    label: "Forget Memory",
    description: "Remove an item from Clawd memory.",
    parameters: Type.Object(
      {
        query: Type.String(),
        section: Type.Optional(Type.String()),
      },
      { additionalProperties: false },
    ),
  },
  {
    command: "list_conflicts",
    name: "list_conflicts",
    label: "List Conflicts",
    description: "List open sync conflicts that need user attention.",
    parameters: Type.Object(
      {
        status: Type.Optional(Type.Union([Type.Literal("open"), Type.Literal("resolved"), Type.Literal("all")])),
      },
      { additionalProperties: false },
    ),
  },
  {
    command: "read_conflict",
    name: "read_conflict",
    label: "Read Conflict",
    description: "Show the latest or a specific sync conflict, including options for resolving it.",
    parameters: Type.Object(
      {
        conflict_id: Type.Optional(Type.String()),
      },
      { additionalProperties: false },
    ),
  },
  {
    command: "resolve_conflict",
    name: "resolve_conflict",
    label: "Resolve Conflict",
    description: "Resolve a sync conflict. Only use keep_local or keep_remote after the user explicitly chooses that strategy.",
    parameters: Type.Object(
      {
        conflict_id: Type.Optional(Type.String()),
        strategy: Type.Union([
          Type.Literal("retry_sync"),
          Type.Literal("keep_local"),
          Type.Literal("keep_remote"),
        ]),
      },
      { additionalProperties: false },
    ),
  },
];

const plugin = {
  id: "clawd-obsidian",
  name: "Clawd Obsidian",
  description: "Bridge Clawd's Python Obsidian workflows into OpenClaw.",
  configSchema: pluginConfigSchema,
  register(api: OpenClawPluginApi) {
    for (const definition of toolDefinitions) {
      api.registerTool(createBridgeTool({ ...definition, api }));
    }
    api.registerCommand(createIdeaCommand(api));
    api.on("before_prompt_build", async () => ({
      prependSystemContext: CLAWD_OBSIDIAN_GUIDANCE,
    }));
  },
};

export default plugin;
