import json
import os
import re

import boto3

from clawd_ops.conflicts import list_conflicts, read_conflict, resolve_conflict
from clawd_ops.exchange import (
    is_duke_email_connected,
    tool_list_duke_email,
    tool_read_duke_email,
    tool_search_duke_email,
)
from clawd_ops.gmail_watcher import (
    tool_check_latest_gmail,
    tool_search_gmail,
)
from clawd_ops.google_auth import (
    finish_google_auth,
    list_google_auth_accounts,
    list_google_auth_credentials,
    set_google_auth_credentials,
    start_google_auth,
)
from clawd_ops.onepassword import (
    get_1password_item,
    list_1password_accounts,
    list_1password_vaults,
    read_1password_secret,
    whoami_1password,
)
from clawd_ops.search import browse_web, search_github_repos, search_papers, search_web
from clawd_ops.vault import (
    add_todos,
    add_world_breaking_idea,
    add_email_filter,
    forget_memory,
    list_files,
    list_email_filters,
    memory_context,
    memory_path,
    remove_email_filter,
    read_memory,
    read_notes,
    read_pdf,
    read_task_list,
    remember_memory,
    save_research,
    task_file_path,
    write_note,
)

BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-opus-4-7")
SYSTEM_PROMPT_BASE = """You are Clawd, a personal Telegram assistant with access to an Obsidian vault and a local persistent memory file.

Core behaviors:
- Be concise, practical, and direct.
- Use tools whenever the user is asking about vault contents, todos, saved memory, or web/paper lookup.
- Only write durable memory when the user explicitly asks you to remember something for future conversations.
- Direct standing preferences about how Clawd should reply, such as formatting or tone changes that should keep applying later, count as explicit durable preferences.
- When the user wants to tune email alerts, use the email filter tools so future Duke/Gmail notifications adapt.
- When the user wants to connect a Google account for Gmail or Calendar, use the Google auth tools.
- When the user asks what you remember about them, use the memory read tool.
- When the user wants to update or create arbitrary markdown files in the vault, use the write_note tool.
- Keep todo items short and actionable. The todo workflow writes into weekly tasks/W##-YYMMDD.md files and supports relative dates like today, yesterday, and tomorrow. Legacy daily tasks/YYMMDD.md files may still exist for reads.
- If a sync conflict exists, use the conflict tools to explain the situation and wait for the user to choose a resolution strategy.
- If a tool fails, explain the failure plainly and propose the next best action.

Email:
- You have access to 4 Gmail accounts and a Duke @duke.edu Outlook/Exchange account.
- When the user says "check my emails", "what came in", or "latest emails", use the check_latest_emails tool. It fetches from ALL accounts at once with full bodies.
- You can search Gmail with search_gmail and Duke email with search_duke_email.
- You can read a specific Duke email by item_id with read_duke_email.
- When an email notification comes in from the watcher, keep it simple: summarize in one sentence and ask if the user wants to add anything to their todos. Don't over-explain.

Calendar:
- Google Calendar is accessible via the exec tool using gog CLI commands.
- The primary calendar account is islam.moh.islamm@gmail.com.
- Always use --account islam.moh.islamm@gmail.com for calendar commands.
- Meeting invites that arrive by email are already on the calendar -- don't notify about them.

Formatting:
- Telegram supports only limited formatting. Keep formatting simple.
- Prefer short paragraphs and plain bullet lists over complex markdown tables.
"""

MEMORY_WRITE_RE = re.compile(
    r"\b("
    r"(?:please\s+)?remember\s+(?:this|that|for\s+future\s+conversations)\b|"
    r"for\s+future\s+conversations[^.?!]*\bremember\b|"
    r"next\s+time\s+remember\b|"
    r"save\s+(?:this|that)\s+(?:to|in)\s+memory\b|"
    r"store\s+(?:this|that)\s+(?:for\s+later|in\s+memory)\b|"
    r"add\s+(?:this|that)\s+to\s+(?:your\s+)?memory\b"
    r")",
    re.IGNORECASE,
)
DIRECT_RESPONSE_PREFERENCE_RE = re.compile(
    r"\b("
    r"(?:stop|don't|do\s+not|avoid|never|no\s+more)\s+"
    r"(?:use|using|include|including|add|adding)\b[^.?!]{0,120}\b"
    r"(?:dash|dashes|em\s+dash|em-dash|symbol|symbols|emoji|emojis|bullet|bullets|markdown|asterisk|asterisks|bold|tone|style|format|formatting)"
    r"|(?:use|keep|make)\s+(?:your\s+)?(?:repl(?:y|ies)|response|responses|message|messages)\b[^.?!]{0,80}\b"
    r"(?:short|brief|concise|plain\s+text|plain-text|direct|simple)"
    r"|(?:be|stay)\s+(?:brief|concise|direct)\b"
    r")",
    re.IGNORECASE,
)
EMAIL_FILTER_UPDATE_RE = re.compile(
    r"\b("
    r"(?:stop|don't|do\s+not|avoid|never)\s+(?:send(?:ing)?|notify(?:ing)?|ping(?:ing)?|alert(?:ing)?)\b[^.?!]{0,160}\b(?:email|emails|newsletter|newsletters|digest|digests|sender|senders|type\s+of\s+emails?)"
    r"|(?:always|please|do)\s+(?:send|notify|ping|alert)\b[^.?!]{0,160}\b(?:email|emails|sender|senders|from)"
    r"|(?:important|not\s+important)\s+(?:email|emails|sender|senders|newsletter|newsletters|digest|digests)"
    r")",
    re.IGNORECASE,
)
EMAIL_FILTER_REMOVE_RE = re.compile(
    r"\b("
    r"(?:remove|delete|forget|undo|clear)\b[^.?!]{0,120}\b(?:email\s+filter|email\s+filters|notification\s+rule|notification\s+rules)"
    r"|(?:start|resume)\s+(?:sending|notifying|pinging|alerting)\b[^.?!]{0,160}\b(?:about|for)"
    r")",
    re.IGNORECASE,
)
GOOGLE_AUTH_RE = re.compile(
    r"\b("
    r"(?:log\s+in|login|sign\s+in|signin|connect|authorize|auth)\b[^.?!]{0,120}\b(?:google|gmail|calendar)"
    r"|(?:google|gmail|calendar)\b[^.?!]{0,120}\b(?:log\s+in|login|sign\s+in|signin|connect|authorize|auth)"
    r")",
    re.IGNORECASE,
)
ONEPASSWORD_RE = re.compile(r"\b(1password|1\s+password|op://)\b", re.IGNORECASE)
MEMORY_FORGET_RE = re.compile(
    r"\b(forget\b|remove .*memory\b|delete .*memory\b|drop .*memory\b)\b",
    re.IGNORECASE,
)

TOOLS = [
    {
        "toolSpec": {
            "name": "add_todos",
            "description": "Add todo items to the current weekly Obsidian task file. For sub-tasks, preserve leading indentation.",
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
                        "query": {
                            "type": "string",
                            "description": "Search query for papers.",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of results.",
                            "default": 5,
                        },
                    },
                    "required": ["query"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "search_web",
            "description": "Search the web for current research sources.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query.",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of results.",
                            "default": 8,
                        },
                    },
                    "required": ["query"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "search_github_repos",
            "description": "Search GitHub repositories relevant to a project, product, or research idea.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query.",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of results.",
                            "default": 8,
                        },
                    },
                    "required": ["query"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "read_task_list",
            "description": "Read task notes from the weekly task workflow, including legacy daily task files when present.",
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
                        "path": {
                            "type": "string",
                            "description": "Relative path to the file in the vault.",
                        },
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
                        "path": {
                            "type": "string",
                            "description": "Relative path inside the vault, such as personal/note.md.",
                        },
                        "content": {
                            "type": "string",
                            "description": "Markdown content to write.",
                        },
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
                        "title": {
                            "type": "string",
                            "description": "Title for the research note.",
                        },
                        "content": {
                            "type": "string",
                            "description": "Markdown content to save.",
                        },
                    },
                    "required": ["title", "content"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "add_world_breaking_idea",
            "description": "Append a world-breaking idea to world-breaking-ideas.md and reserve a research report path.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "idea": {
                            "type": "string",
                            "description": "The original idea text.",
                        },
                        "report_path": {
                            "type": "string",
                            "description": "Optional report path relative to the vault.",
                        },
                    },
                    "required": ["idea"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "list_files",
            "description": "List readable files (markdown and PDF) in an Obsidian vault folder.",
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
            "description": "Read the assistant's persistent memory file.",
            "inputSchema": {
                "json": {"type": "object", "properties": {}, "required": []}
            },
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
                        "memory": {
                            "type": "string",
                            "description": "The thing to remember for future conversations.",
                        },
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
            "name": "add_email_filter",
            "description": "Store a durable email notification rule, such as suppressing newsletters or always notifying on a sender/topic.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": [
                                "allow_sender",
                                "suppress_sender",
                                "allow_topic",
                                "suppress_topic",
                            ],
                            "description": "Type of email notification rule.",
                        },
                        "pattern": {
                            "type": "string",
                            "description": "Substring to match, such as a sender email, sender name, newsletter name, or topic phrase.",
                        },
                    },
                    "required": ["kind", "pattern"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "remove_email_filter",
            "description": "Remove a previously stored email notification rule.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "Substring used to find a stored email filter to remove.",
                        },
                        "kind": {
                            "type": "string",
                            "enum": [
                                "",
                                "allow_sender",
                                "suppress_sender",
                                "allow_topic",
                                "suppress_topic",
                            ],
                            "description": "Optional rule type to restrict the removal.",
                            "default": "",
                        },
                    },
                    "required": ["pattern"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "list_email_filters",
            "description": "List stored email notification rules that affect future alerts.",
            "inputSchema": {
                "json": {"type": "object", "properties": {}, "required": []}
            },
        }
    },
    {
        "toolSpec": {
            "name": "list_google_auth_accounts",
            "description": "List Google accounts currently authenticated through gog.",
            "inputSchema": {
                "json": {"type": "object", "properties": {}, "required": []}
            },
        }
    },
    {
        "toolSpec": {
            "name": "list_google_auth_credentials",
            "description": "List stored Google OAuth client credentials available to gog.",
            "inputSchema": {
                "json": {"type": "object", "properties": {}, "required": []}
            },
        }
    },
    {
        "toolSpec": {
            "name": "set_google_auth_credentials",
            "description": "Store a Google OAuth client credentials JSON file for gog, using a server-side path.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "credentials_path": {
                            "type": "string",
                            "description": "Absolute path on the server to a Google OAuth client JSON file.",
                        }
                    },
                    "required": ["credentials_path"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "start_google_auth",
            "description": "Start a remote/server-friendly Google OAuth flow through gog for a Gmail/Calendar account.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "email": {
                            "type": "string",
                            "description": "Google account email address to authorize.",
                        },
                        "services": {
                            "type": "string",
                            "description": "Comma-separated services, such as gmail,calendar.",
                            "default": "gmail,calendar",
                        },
                        "readonly": {
                            "type": "boolean",
                            "description": "Use read-only scopes where available.",
                            "default": False,
                        },
                        "client": {
                            "type": "string",
                            "description": "Optional stored OAuth client name.",
                            "default": "",
                        },
                    },
                    "required": ["email"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "finish_google_auth",
            "description": "Finish a remote Google OAuth flow through gog after the user pastes back the redirect URL.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "email": {
                            "type": "string",
                            "description": "Google account email address being authorized.",
                        },
                        "auth_url": {
                            "type": "string",
                            "description": "Full redirect URL returned after the user finishes the browser sign-in.",
                        },
                        "services": {
                            "type": "string",
                            "description": "Comma-separated services, such as gmail,calendar.",
                            "default": "gmail,calendar",
                        },
                        "readonly": {
                            "type": "boolean",
                            "description": "Use read-only scopes where available.",
                            "default": False,
                        },
                        "client": {
                            "type": "string",
                            "description": "Optional stored OAuth client name.",
                            "default": "",
                        },
                    },
                    "required": ["email", "auth_url"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "list_1password_accounts",
            "description": "List 1Password accounts available to the CLI on this machine.",
            "inputSchema": {
                "json": {"type": "object", "properties": {}, "required": []}
            },
        }
    },
    {
        "toolSpec": {
            "name": "whoami_1password",
            "description": "Show the currently signed-in 1Password account, if any.",
            "inputSchema": {
                "json": {"type": "object", "properties": {}, "required": []}
            },
        }
    },
    {
        "toolSpec": {
            "name": "list_1password_vaults",
            "description": "List 1Password vaults visible to the currently signed-in account.",
            "inputSchema": {
                "json": {"type": "object", "properties": {}, "required": []}
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_1password_item",
            "description": "Read a 1Password item by name or UUID.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "item": {
                            "type": "string",
                            "description": "Item title or UUID.",
                        },
                        "vault": {
                            "type": "string",
                            "description": "Optional vault name or UUID.",
                            "default": "",
                        },
                    },
                    "required": ["item"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "read_1password_secret",
            "description": "Read a 1Password secret reference such as op://vault/item/field.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "secret_reference": {
                            "type": "string",
                            "description": "1Password secret reference beginning with op://",
                        }
                    },
                    "required": ["secret_reference"],
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
                        "query": {
                            "type": "string",
                            "description": "Substring or phrase used to find memories to remove.",
                        },
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
    {
        "toolSpec": {
            "name": "list_conflicts",
            "description": "List open sync conflicts that need user attention.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["open", "resolved", "all"],
                            "default": "open",
                        }
                    },
                    "required": [],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "read_conflict",
            "description": "Read the latest or a specific sync conflict, including resolution options.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "conflict_id": {
                            "type": "string",
                            "description": "Optional conflict id. Defaults to the latest open conflict.",
                            "default": "latest",
                        }
                    },
                    "required": [],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "resolve_conflict",
            "description": "Resolve a sync conflict. Only use keep_local or keep_remote after the user explicitly chooses that strategy.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "conflict_id": {
                            "type": "string",
                            "description": "Optional conflict id. Defaults to the latest open conflict.",
                            "default": "latest",
                        },
                        "strategy": {
                            "type": "string",
                            "enum": ["retry_sync", "keep_local", "keep_remote"],
                            "description": "How to resolve the conflict.",
                        },
                    },
                    "required": ["strategy"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "read_pdf",
            "description": "Read a PDF file from the Obsidian vault and return its text content. Use for research papers, syllabi, or any PDF documents stored in the vault.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Path to the PDF file within the vault.",
                        },
                        "max_chars": {
                            "type": "integer",
                            "description": "Maximum characters to extract. Defaults to 50000.",
                            "default": 50000,
                        },
                    },
                    "required": ["path"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "check_latest_emails",
            "description": "Fetch the latest emails from ALL connected accounts (Gmail + Duke Outlook) with full bodies. Use when the user says 'check my emails', 'what came in', 'latest emails', etc.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "max_per_account": {
                            "type": "integer",
                            "description": "Max emails per account. Defaults to 5.",
                            "default": 5,
                        },
                    },
                    "required": [],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "search_gmail",
            "description": "Search Gmail by query across all Gmail accounts (or a specific one). Supports Gmail search syntax like from:, subject:, newer_than:, etc.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Gmail search query, e.g. 'from:professor subject:grade' or 'newer_than:3d has:attachment'.",
                        },
                        "account": {
                            "type": "string",
                            "description": "Specific Gmail account to search. Empty searches all accounts.",
                            "default": "",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Max results. Defaults to 10.",
                            "default": 10,
                        },
                    },
                    "required": ["query"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "search_duke_email",
            "description": "Search Duke @duke.edu email by keyword. Uses Outlook/EWS query syntax (same as Outlook search bar). Returns results with sender, subject, date, and body snippet.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query, e.g. 'from:registrar' or 'subject:grade report'.",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Max results. Defaults to 10.",
                            "default": 10,
                        },
                    },
                    "required": ["query"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "read_duke_email",
            "description": "Read a single Duke email by its item_id (returned from search_duke_email or check_latest_emails). Returns the full body.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "item_id": {
                            "type": "string",
                            "description": "The EWS item_id of the email to read.",
                        },
                    },
                    "required": ["item_id"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "list_duke_email",
            "description": "List recent Duke @duke.edu inbox emails. Use when the user asks about their Duke email or university inbox.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "max_results": {
                            "type": "integer",
                            "description": "Max emails to list. Defaults to 10.",
                            "default": 10,
                        },
                    },
                    "required": [],
                }
            },
        }
    },
]

TOOL_FUNCTIONS = {
    "add_todos": lambda items, target_date="today": add_todos(items, target_date),
    "add_world_breaking_idea": lambda idea, report_path=None: add_world_breaking_idea(
        idea, report_path
    ),
    "add_email_filter": lambda kind, pattern: add_email_filter(kind, pattern),
    "finish_google_auth": lambda email, auth_url, services="gmail,calendar", readonly=False, client="": (
        finish_google_auth(email, auth_url, services, readonly, client)
    ),
    "get_1password_item": lambda item, vault="": get_1password_item(item, vault),
    "list_1password_accounts": lambda: list_1password_accounts(),
    "list_1password_vaults": lambda: list_1password_vaults(),
    "search_github_repos": lambda query, max_results=8: search_github_repos(
        query, max_results
    ),
    "search_papers": lambda query, max_results=5: search_papers(query, max_results),
    "search_web": lambda query, max_results=8: search_web(query, max_results),
    "read_task_list": lambda target_date="today": read_task_list(target_date),
    "read_notes": lambda path: read_notes(path),
    "write_note": lambda path, content, mode="overwrite": write_note(
        path, content, mode
    ),
    "save_research": lambda title, content: save_research(title, content),
    "list_files": lambda folder="": list_files(folder),
    "browse_web": lambda url: browse_web(url),
    "list_google_auth_accounts": lambda: list_google_auth_accounts(),
    "list_google_auth_credentials": lambda: list_google_auth_credentials(),
    "list_email_filters": lambda: list_email_filters(),
    "read_memory": lambda: read_memory(),
    "remember_memory": lambda memory, section="Preferences": remember_memory(
        memory, section
    ),
    "remove_email_filter": lambda pattern, kind="": remove_email_filter(pattern, kind),
    "set_google_auth_credentials": lambda credentials_path: set_google_auth_credentials(
        credentials_path
    ),
    "start_google_auth": lambda email, services="gmail,calendar", readonly=False, client="": (
        start_google_auth(email, services, readonly, client)
    ),
    "read_1password_secret": lambda secret_reference: read_1password_secret(
        secret_reference
    ),
    "whoami_1password": lambda: whoami_1password(),
    "forget_memory": lambda query, section="": forget_memory(query, section),
    "list_conflicts": lambda status="open": list_conflicts(status),
    "read_conflict": lambda conflict_id="latest": read_conflict(conflict_id),
    "resolve_conflict": lambda conflict_id="latest", strategy="retry_sync": (
        resolve_conflict(conflict_id, strategy)
    ),
    "read_pdf": lambda path, max_chars=50000: read_pdf(path, max_chars),
    "check_latest_emails": lambda max_per_account=5: _check_all_emails(max_per_account),
    "search_gmail": lambda query, account="", max_results=10: tool_search_gmail(
        query, account, max_results
    ),
    "search_duke_email": lambda query, max_results=10: tool_search_duke_email(
        query, max_results
    ),
    "read_duke_email": lambda item_id: tool_read_duke_email(item_id),
    "list_duke_email": lambda max_results=10: tool_list_duke_email(max_results),
}


def _check_all_emails(max_per_account: int = 5) -> str:
    """Unified tool: fetch latest from all Gmail accounts + Duke email."""
    parts: list[str] = []

    # Gmail accounts
    gmail_result = tool_check_latest_gmail(max_per_account)
    if gmail_result:
        parts.append("=== GMAIL ===")
        parts.append(gmail_result)

    # Duke email
    if is_duke_email_connected():
        duke_result = tool_list_duke_email(max_per_account)
        parts.append("")
        parts.append("=== DUKE EMAIL ===")
        parts.append(duke_result)
    else:
        parts.append("")
        parts.append("=== DUKE EMAIL ===")
        parts.append("Duke email is not connected.")

    return "\n".join(parts)


def tool_specs() -> list[dict]:
    return TOOLS


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
    return history[-(max_turns * 2) :]


def _extract_text(content: list[dict]) -> str:
    texts = [
        block["text"].strip()
        for block in content
        if "text" in block and block["text"].strip()
    ]
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


def _allow_memory_write(user_text: str) -> bool:
    return bool(
        MEMORY_WRITE_RE.search(user_text)
        or DIRECT_RESPONSE_PREFERENCE_RE.search(user_text)
    )


def _allow_email_filter_update(user_text: str) -> bool:
    return bool(EMAIL_FILTER_UPDATE_RE.search(user_text))


def _allow_email_filter_remove(user_text: str) -> bool:
    return bool(EMAIL_FILTER_REMOVE_RE.search(user_text))


def _allow_google_auth(user_text: str) -> bool:
    return bool(GOOGLE_AUTH_RE.search(user_text))


def _allow_1password(user_text: str) -> bool:
    return bool(ONEPASSWORD_RE.search(user_text))


def _allow_memory_forget(user_text: str) -> bool:
    return bool(MEMORY_FORGET_RE.search(user_text))


def _execute_tool(tool_name: str, tool_input: dict, user_text: str):
    if tool_name == "remember_memory" and not _allow_memory_write(user_text):
        raise ValueError(
            "Persistent memory can only be updated when the user explicitly asks to remember something."
        )
    if tool_name == "add_email_filter" and not _allow_email_filter_update(user_text):
        raise ValueError(
            "Email filters can only be updated when the user explicitly asks to change email notifications."
        )
    if tool_name == "remove_email_filter" and not _allow_email_filter_remove(user_text):
        raise ValueError(
            "Email filters can only be removed when the user explicitly asks to remove or undo an email notification rule."
        )
    if tool_name in {
        "set_google_auth_credentials",
        "start_google_auth",
        "finish_google_auth",
    } and not _allow_google_auth(user_text):
        raise ValueError(
            "Google auth changes can only run when the user explicitly asks to sign in or connect a Google account."
        )
    if tool_name in {
        "list_1password_accounts",
        "whoami_1password",
        "list_1password_vaults",
        "get_1password_item",
        "read_1password_secret",
    } and not _allow_1password(user_text):
        raise ValueError(
            "1Password access can only run when the user explicitly asks to use 1Password."
        )
    if tool_name == "forget_memory" and not _allow_memory_forget(user_text):
        raise ValueError(
            "Persistent memory can only be removed when the user explicitly asks to forget or remove it."
        )
    return TOOL_FUNCTIONS[tool_name](**tool_input)


def _process_message_with_history(
    user_text: str,
    conversation_history: list[dict] | None = None,
) -> tuple[str, list[dict]]:
    client = _bedrock_client()

    persistent_history = [
        _clone_message(message) for message in (conversation_history or [])
    ]
    messages = [_clone_message(message) for message in persistent_history]

    user_message = _message("user", user_text)
    messages.append(_clone_message(user_message))
    persistent_history.append(user_message)

    while True:
        response = client.converse(**build_converse_request(messages))
        output = response["output"]["message"]
        messages.append(_clone_message(output))

        if response["stopReason"] != "tool_use":
            final_text = (
                _extract_text(output["content"]) or "I couldn't produce a response."
            )
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
                result = _execute_tool(tool_name, tool_input, user_text)
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
    response_text, updated_history = _process_message_with_history(
        user_text, conversation_history
    )
    if return_history:
        return response_text, updated_history
    return response_text
