import json
import os

import boto3

from obsidian import (
    add_todos,
    forget_memory,
    list_files,
    memory_context,
    memory_path,
    read_task_list,
    read_memory,
    read_notes,
    remember_memory,
    save_research,
    task_file_path,
    write_note,
)
from search import browse_web, search_papers

BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-opus-4-6-v1")
SYSTEM_PROMPT_BASE = """You are Clawd, a personal Telegram assistant with access to an Obsidian vault.

Core behaviors:
- Be concise, practical, and direct.
- Use tools whenever the user is asking about vault contents, todos, saved memory, or web/paper lookup.
- When the user asks you to remember something for future conversations, use the memory tools.
- When the user asks what you remember about them, use the memory read tool.
- When the user wants to update or create arbitrary markdown files in the vault, use the write_note tool.
- Keep todo items short and actionable. The todo workflow writes into tasks/MMDDYY.md files and supports relative dates like today, yesterday, and tomorrow.
- If a tool fails, explain the failure plainly and propose the next best action.

Formatting:
- Telegram supports only limited formatting. Keep formatting simple.
- Prefer short paragraphs and plain bullet lists over complex markdown tables.
"""

TOOLS = [
    {
        "toolSpec": {
            "name": "add_todos",
            "description": "Add todo items to today's Obsidian tasks/MMDDYY.md file. For sub-tasks, prefix an item with a tab character.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of todo items to add to today's task note.",
                        },
                        "target_date": {
                            "type": "string",
                            "description": "Optional task date such as today, yesterday, tomorrow, 2026-03-10, or March 10, 2026.",
                            "default": "today",
                        },
                    },
                    "required": ["items"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "search_papers",
            "description": "Search for academic papers across arXiv and Google Scholar.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query for papers."},
                        "max_results": {"type": "integer", "description": "Maximum number of results.", "default": 5},
                    },
                    "required": ["query"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "read_task_list",
            "description": "Read a dated task note from the tasks/MMDDYY.md workflow.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "target_date": {
                            "type": "string",
                            "description": "Task date such as today, yesterday, tomorrow, 2026-03-10, or March 10, 2026.",
                            "default": "today",
                        },
                    },
                    "required": [],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "read_notes",
            "description": "Read a file from the Obsidian vault. The path is relative to the vault root.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path to the file in the vault."},
                    },
                    "required": ["path"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "write_note",
            "description": "Create or update any file in the Obsidian vault.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path inside the vault, such as personal/note.md."},
                        "content": {"type": "string", "description": "Markdown content to write."},
                        "mode": {
                            "type": "string",
                            "description": "How to apply the content: overwrite, append, or prepend.",
                            "enum": ["overwrite", "append", "prepend"],
                            "default": "overwrite",
                        },
                    },
                    "required": ["path", "content"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "save_research",
            "description": "Save a research summary into research/<slug>.md in the Obsidian vault.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Title for the research note."},
                        "content": {"type": "string", "description": "Markdown content to save."},
                    },
                    "required": ["title", "content"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "list_files",
            "description": "List markdown files in an Obsidian vault folder.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "folder": {
                            "type": "string",
                            "description": "Folder path relative to the vault root. Empty string lists from the root.",
                            "default": "",
                        },
                    },
                    "required": [],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "browse_web",
            "description": "Fetch and extract readable text from a web page URL.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "The URL to fetch."},
                    },
                    "required": ["url"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "read_memory",
            "description": "Read the assistant's persistent memory file from the Obsidian vault.",
            "inputSchema": {"json": {"type": "object", "properties": {}, "required": []}},
        }
    },
    {
        "toolSpec": {
            "name": "remember_memory",
            "description": "Store a durable user preference or long-lived fact in the assistant memory file.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "memory": {"type": "string", "description": "The thing to remember for future conversations."},
                        "section": {
                            "type": "string",
                            "description": "Section name to group the memory under, such as Preferences, Tone, Projects, or Open Loops.",
                            "default": "Preferences",
                        },
                    },
                    "required": ["memory"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "forget_memory",
            "description": "Remove a persistent memory item by matching part of its text.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Substring or phrase used to find memories to remove."},
                        "section": {
                            "type": "string",
                            "description": "Optional section name to restrict the removal.",
                            "default": "",
                        },
                    },
                    "required": ["query"],
                }
            },
        }
    },
]

TOOL_FUNCTIONS = {
    "add_todos": lambda items, target_date="today": add_todos(items, target_date),
    "search_papers": lambda query, max_results=5: search_papers(query, max_results),
    "read_task_list": lambda target_date="today": read_task_list(target_date),
    "read_notes": lambda path: read_notes(path),
    "write_note": lambda path, content, mode="overwrite": write_note(path, content, mode),
    "save_research": lambda title, content: save_research(title, content),
    "list_files": lambda folder="": list_files(folder),
    "browse_web": lambda url: browse_web(url),
    "read_memory": lambda: read_memory(),
    "remember_memory": lambda memory, section="Preferences": remember_memory(memory, section),
    "forget_memory": lambda query, section="": forget_memory(query, section),
}


def _bedrock_client():
    return boto3.client(
        "bedrock-runtime",
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
    )


def _message(role: str, text: str) -> dict:
    return {"role": role, "content": [{"text": text}]}


def _clone_message(message: dict) -> dict:
    return json.loads(json.dumps(message))


def _trim_history(history: list[dict], max_turns: int = 10) -> list[dict]:
    return history[-(max_turns * 2):]


def _extract_text(content: list[dict]) -> str:
    texts = [block["text"].strip() for block in content if "text" in block and block["text"].strip()]
    return "\n".join(texts).strip()


def _tool_result_content(result) -> list[dict]:
    if isinstance(result, str):
        return [{"text": result}]
    if result is None or isinstance(result, (bool, int, float, dict, list)):
        return [{"json": result}]
    return [{"text": str(result)}]


def _system_prompt() -> str:
    memory = memory_context(sync=True)
    return (
        f"{SYSTEM_PROMPT_BASE}\n"
        f"Today's task note path is {task_file_path('today')}.\n"
        f"Yesterday's task note path is {task_file_path('yesterday')}.\n"
        f"Persistent memory file path: {memory_path()}\n\n"
        f"Persistent memory:\n{memory}"
    )


def build_converse_request(messages: list[dict]) -> dict:
    return {
        "modelId": BEDROCK_MODEL_ID,
        "system": [{"text": _system_prompt()}],
        "messages": messages,
        "toolConfig": {"tools": TOOLS},
    }


def _process_message_with_history(
    user_text: str,
    conversation_history: list[dict] | None = None,
) -> tuple[str, list[dict]]:
    client = _bedrock_client()

    persistent_history = [_clone_message(message) for message in (conversation_history or [])]
    messages = [_clone_message(message) for message in persistent_history]

    user_message = _message("user", user_text)
    messages.append(_clone_message(user_message))
    persistent_history.append(user_message)

    while True:
        response = client.converse(**build_converse_request(messages))
        output = response["output"]["message"]
        messages.append(_clone_message(output))

        if response["stopReason"] != "tool_use":
            final_text = _extract_text(output["content"]) or "I couldn't produce a response."
            persistent_history.append(_message("assistant", final_text))
            return final_text, _trim_history(persistent_history)

        tool_results = []
        for block in output["content"]:
            if "toolUse" not in block:
                continue

            tool = block["toolUse"]
            tool_name = tool["name"]
            tool_input = tool["input"]
            tool_use_id = tool["toolUseId"]

            try:
                result = TOOL_FUNCTIONS[tool_name](**tool_input)
                tool_results.append(
                    {
                        "toolResult": {
                            "toolUseId": tool_use_id,
                            "content": _tool_result_content(result),
                        }
                    }
                )
            except Exception as exc:
                tool_results.append(
                    {
                        "toolResult": {
                            "toolUseId": tool_use_id,
                            "content": [{"text": f"Error: {exc}"}],
                            "status": "error",
                        }
                    }
                )

        messages.append({"role": "user", "content": tool_results})


def process_message(
    user_text: str,
    conversation_history: list[dict] | None = None,
    return_history: bool = False,
) -> str | tuple[str, list[dict]]:
    response_text, updated_history = _process_message_with_history(user_text, conversation_history)
    if return_history:
        return response_text, updated_history
    return response_text
