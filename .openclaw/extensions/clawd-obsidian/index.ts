import { Type } from "@sinclair/typebox";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/core";
import { bridgeResultToToolResult, resolveBridgeConfig, runBridgeCommand } from "./src/bridge.js";
import { CLAWD_OBSIDIAN_GUIDANCE } from "./src/prompt-guidance.js";

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
    api.on("before_prompt_build", async () => ({
      prependSystemContext: CLAWD_OBSIDIAN_GUIDANCE,
    }));
  },
};

export default plugin;
