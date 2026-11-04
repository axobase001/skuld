import { createHash, randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import { promises as fs } from "node:fs";
import path from "node:path";
import { promisify } from "node:util";

import {
  createBashToolDefinition,
  createEditToolDefinition,
  createFindToolDefinition,
  createGrepToolDefinition,
  createLsToolDefinition,
  createReadToolDefinition,
  createWriteToolDefinition,
  type ToolDefinition,
} from "@earendil-works/pi-coding-agent";

export type SessionCapabilityWire = {
  session_id: string;
  requested_cwd: string;
  resolved_cwd: string;
  allowed_roots: string[];
  denied_roots: string[];
  issued_at: number;
  expiry: number;
  capability_hash: string;
};

export type ActionCapabilityWire = {
  session_id: string;
  action_id: string;
  plan_hash: string;
  resolved_effect_class: string;
  data_class: string;
  read_scope?: string | null;
  remote_model_visible: boolean;
  authorized_operations: string[];
  declared_targets: string[];
  resolved_allowed_targets: string[];
  session_allowed_roots: string[];
  issued_at: number;
  expiry: number;
  capability_hash: string;
};

type ToolReceipt = {
  type: "tool_receipt";
  receipt_id: string;
  session_id: string;
  action_id: string;
  plan_hash: string;
  tool: string;
  requested_targets: string[];
  resolved_targets: string[];
  bytes_read: number;
  content_sha256?: string;
  command_exit_code?: number;
  data_class: string;
  remote_model_visible: boolean;
  decision: "ALLOWED" | "BLOCKED";
  decision_code: string;
  started_at: string;
  finished_at: string;
  raw_content_persisted: false;
  raw_content_in_receipt: false;
};

type EgressReceipt = {
  type: "network_egress_receipt";
  egress_id: string;
  observation_refs: string[];
  tool_receipt_refs: string[];
  provider: string;
  model: string;
  data_class: string;
  payload_bytes: number;
  estimated_tokens: number;
  redaction_version: string;
  decision: "ALLOWED";
  decision_code: string;
  created_at: string;
};

type ReceiptEmitter = (receipt: ToolReceipt | EgressReceipt) => void;

type GuardState = {
  session: SessionCapabilityWire;
  currentAction: () => ActionCapabilityWire | undefined;
  currentParameters?: () => Record<string, unknown> | undefined;
  provider: string;
  model: string;
  emit: ReceiptEmitter;
};

type OperationResult = {
  text: string;
  bytesRead: number;
  contentHash?: string;
  exitCode?: number;
};

const MAX_TEXT_BYTES = 50 * 1024;
const MAX_SCAN_BYTES = 32 * 1024 * 1024;
const MAX_SCAN_FILES = 5000;

function canonicalValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (value !== null && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return Object.fromEntries(
      Object.keys(record)
        .sort()
        .map((key) => [key, canonicalValue(record[key])]),
    );
  }
  return value;
}

function canonicalHash(value: Record<string, unknown>): string {
  return createHash("sha256")
    .update(JSON.stringify(canonicalValue(value)), "utf8")
    .digest("hex");
}

function verifyCapabilityHash(value: Record<string, unknown>): boolean {
  const { capability_hash: supplied, ...payload } = value;
  return typeof supplied === "string" && supplied === canonicalHash(payload);
}

function normalize(value: string): string {
  const normalized = path.normalize(value);
  return process.platform === "win32"
    ? normalized.toLocaleLowerCase("en-US")
    : normalized;
}

function within(target: string, root: string): boolean {
  const relative = path.relative(normalize(root), normalize(target));
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function syntaxError(raw: string): string | undefined {
  if (!raw || raw.includes("\0")) return "INVALID_PATH";
  if (process.platform !== "win32") {
    const absolute = path.isAbsolute(raw) ? path.normalize(raw) : path.resolve("/", raw);
    if (/^\/proc\/(?:self|[0-9]+)\/fd(?:\/|$)/.test(absolute)) {
      return "PROC_FD_DENIED";
    }
    if (raw.split("/").includes("..")) return "TRAVERSAL_DENIED";
    return undefined;
  }
  const windows = raw.replaceAll("/", "\\");
  if (windows.startsWith("\\\\?\\") || windows.startsWith("\\\\.\\")) {
    return "DEVICE_PATH_DENIED";
  }
  if (windows.startsWith("\\\\")) return "UNC_PATH_DENIED";
  const [, tail] = path.win32.parse(path.win32.resolve(windows)).root
    ? path.win32.parse(path.win32.resolve(windows)).root.split(":")
    : [undefined, undefined];
  void tail;
  const driveRelative = windows.replace(/^[A-Za-z]:/, "");
  if (driveRelative.includes(":")) return "ADS_DENIED";
  const parts = windows.split("\\").filter(Boolean);
  if (parts.includes("..")) return "TRAVERSAL_DENIED";
  if (parts.some((part) => part.endsWith(".") || part.endsWith(" "))) {
    return "TRAILING_DOT_SPACE_DENIED";
  }
  if (parts.some((part) => /~[0-9](?:\.|$)/i.test(part))) {
    return "SHORT_NAME_ALIAS_DENIED";
  }
  return undefined;
}

async function realTarget(cwd: string, requested: string, mustExist = true): Promise<string> {
  const syntax = syntaxError(requested);
  if (syntax) throw new Error(syntax);
  const absolute = path.isAbsolute(requested)
    ? path.normalize(requested)
    : path.resolve(cwd, requested);
  if (mustExist) return fs.realpath(absolute);
  const parent = await fs.realpath(path.dirname(absolute));
  return path.join(parent, path.basename(absolute));
}

function validateSession(session: SessionCapabilityWire): void {
  if (!verifyCapabilityHash(session as unknown as Record<string, unknown>)) {
    throw new Error("SESSION_CAPABILITY_HASH_INVALID");
  }
  if (!Number.isSafeInteger(session.expiry) || session.expiry <= Date.now()) {
    throw new Error("SESSION_CAPABILITY_EXPIRED");
  }
  if (!session.allowed_roots.length) throw new Error("SESSION_ALLOWED_ROOTS_EMPTY");
  if (!within(session.resolved_cwd, session.allowed_roots[0] ?? "")) {
    const allowed = session.allowed_roots.some((root) => within(session.resolved_cwd, root));
    if (!allowed) throw new Error("SESSION_START_DENIED");
  }
  if (session.denied_roots.some((root) => within(session.resolved_cwd, root))) {
    throw new Error("SESSION_START_DENIED");
  }
}

function validateAction(
  action: ActionCapabilityWire | undefined,
  session: SessionCapabilityWire,
  operation: string,
): ActionCapabilityWire {
  if (!action) throw new Error("ACTION_CAPABILITY_MISSING");
  if (!verifyCapabilityHash(action as unknown as Record<string, unknown>)) {
    throw new Error("ACTION_CAPABILITY_HASH_INVALID");
  }
  if (action.session_id !== session.session_id) throw new Error("SESSION_BINDING_MISMATCH");
  if (action.expiry <= Date.now()) throw new Error("ACTION_CAPABILITY_EXPIRED");
  const readOperations = new Set(["read", "grep", "find", "ls"]);
  const expectedEffect = readOperations.has(operation) ? "READ_ONLY" : "LOCAL_REVERSIBLE";
  if (!action.authorized_operations.includes(operation) && !action.authorized_operations.includes(`skuld_${operation}`)) {
    throw new Error("OPERATION_NOT_AUTHORIZED");
  }
  const exactSupportingRead =
    operation === "read" &&
    action.resolved_effect_class === "LOCAL_REVERSIBLE" &&
    action.authorized_operations.includes("read") &&
    action.read_scope === "READ_ONLY_DECLARED_NARROW" &&
    action.remote_model_visible;
  if (action.resolved_effect_class !== expectedEffect && !exactSupportingRead) {
    throw new Error("WORLD_EFFECT_DENIED");
  }
  if (operation === "shell" && !action.remote_model_visible) {
    throw new Error("REMOTE_OUTPUT_UNDECLARED");
  }
  if (action.data_class === "UNKNOWN_DATA_CLASS") throw new Error("UNKNOWN_DATA_CLASS");
  if (readOperations.has(operation) && action.remote_model_visible && action.data_class === "SECRET_DATA") {
    throw new Error("SECRET_EGRESS_DENIED");
  }
  return action;
}

async function guardTarget(
  requested: string,
  operation: string,
  state: GuardState,
  mustExist = true,
): Promise<{ action: ActionCapabilityWire; resolved: string }> {
  const action = validateAction(state.currentAction(), state.session, operation);
  const resolved = await realTarget(state.session.resolved_cwd, requested, mustExist);
  if (!state.session.allowed_roots.some((root) => within(resolved, root))) {
    throw new Error("TARGET_OUTSIDE_ALLOWED_ROOTS");
  }
  if (state.session.denied_roots.some((root) => within(resolved, root))) {
    throw new Error("DENIED_RESOURCE_CLASS");
  }
  if (!action.resolved_allowed_targets.some((root) => within(resolved, root))) {
    throw new Error("TARGET_OUTSIDE_ACTION_CAPABILITY");
  }
  if (operation === "write" && state.currentParameters?.()?.supporting_write === true) {
    const targets = state.currentParameters?.()?.create_targets;
    if (!Array.isArray(targets) || !targets.some(target => typeof target === "string" && normalize(target) === normalize(resolved))) {
      throw new Error("CREATE_TARGET_NOT_DECLARED");
    }
  } else if (operation === "write" && state.currentParameters?.()?.primary_operation === "edit") {
    throw new Error("CREATE_SCOPE_REQUIRED");
  }
  if (mustExist) {
    const stat = await fs.stat(resolved);
    if (stat.isFile() && stat.nlink > 1) throw new Error("HARDLINK_AMBIGUOUS");
  }
  return { action, resolved };
}

function truncate(text: string): string {
  const buffer = Buffer.from(text, "utf8");
  if (buffer.length <= MAX_TEXT_BYTES) return text;
  return `${buffer.subarray(0, MAX_TEXT_BYTES).toString("utf8")}\n[truncated]`;
}

function globRegex(pattern: string): RegExp {
  const escaped = pattern
    .replace(/[.+^${}()|[\]\\]/g, "\\$&")
    .replaceAll("**", "\u0000")
    .replaceAll("*", "[^\\\\/]*")
    .replaceAll("?", "[^\\\\/]")
    .replaceAll("\u0000", ".*");
  return new RegExp(`^${escaped}$`, "i");
}

async function walkFiles(root: string): Promise<string[]> {
  const result: string[] = [];
  const queue = [root];
  while (queue.length) {
    const current = queue.shift();
    if (!current) break;
    const entries = await fs.readdir(current, { withFileTypes: true });
    for (const entry of entries) {
      const candidate = path.join(current, entry.name);
      if (entry.isSymbolicLink()) continue;
      const resolved = await fs.realpath(candidate);
      if (!within(resolved, root)) throw new Error("REPARSE_POINT_ESCAPE");
      if (entry.isDirectory()) queue.push(resolved);
      else if (entry.isFile()) result.push(resolved);
      if (result.length + queue.length > MAX_SCAN_FILES) {
        throw new Error("SCAN_FILE_LIMIT_EXCEEDED");
      }
    }
  }
  return result;
}

function emitSuccess(
  state: GuardState,
  action: ActionCapabilityWire,
  operation: string,
  requested: string,
  resolved: string,
  startedAt: string,
  result: OperationResult,
): void {
  const receiptId = `receipt_${randomUUID()}`;
  const receipt: ToolReceipt = {
    type: "tool_receipt",
    receipt_id: receiptId,
    session_id: action.session_id,
    action_id: action.action_id,
    plan_hash: action.plan_hash,
    tool: operation,
    requested_targets: [requested],
    resolved_targets: [resolved],
    bytes_read: result.bytesRead,
    ...(result.contentHash ? { content_sha256: result.contentHash } : {}),
    ...(result.exitCode !== undefined ? { command_exit_code: result.exitCode } : {}),
    data_class: action.data_class,
    remote_model_visible: action.remote_model_visible,
    decision: "ALLOWED",
    decision_code: "TOOL_CALL_COMPLETED",
    started_at: startedAt,
    finished_at: new Date().toISOString(),
    raw_content_persisted: false,
    raw_content_in_receipt: false,
  };
  state.emit(receipt);
  if (action.remote_model_visible) {
    const payloadBytes = Buffer.byteLength(result.text, "utf8");
    state.emit({
      type: "network_egress_receipt",
      egress_id: `egress_${randomUUID()}`,
      observation_refs: [],
      tool_receipt_refs: [receiptId],
      provider: state.provider,
      model: state.model,
      data_class: action.data_class,
      payload_bytes: payloadBytes,
      estimated_tokens: Math.ceil(payloadBytes / 4),
      redaction_version: "terminal-egress-v1",
      decision: "ALLOWED",
      decision_code: "CAPABILITY_BOUND_TOOL_RESULT",
      created_at: new Date().toISOString(),
    });
  }
}

function emitBlocked(
  state: GuardState,
  operation: string,
  requested: string,
  startedAt: string,
  error: unknown,
): void {
  const action = state.currentAction();
  if (!action) return;
  state.emit({
    type: "tool_receipt",
    receipt_id: `receipt_${randomUUID()}`,
    session_id: action.session_id,
    action_id: action.action_id,
    plan_hash: action.plan_hash,
    tool: operation,
    requested_targets: [requested],
    resolved_targets: [],
    bytes_read: 0,
    data_class: action.data_class,
    remote_model_visible: false,
    decision: "BLOCKED",
    decision_code: error instanceof Error ? error.message : "TOOL_CALL_BLOCKED",
    started_at: startedAt,
    finished_at: new Date().toISOString(),
    raw_content_persisted: false,
    raw_content_in_receipt: false,
  });
}

async function runGuarded(
  state: GuardState,
  operation: string,
  requested: string,
  execute: (resolved: string) => Promise<OperationResult>,
  mustExist = true,
): Promise<{ content: [{ type: "text"; text: string }]; details: Record<string, never> }> {
  const startedAt = new Date().toISOString();
  try {
    const { action, resolved } = await guardTarget(requested, operation, state, mustExist);
    const result = await execute(resolved);
    emitSuccess(state, action, operation, requested, resolved, startedAt, result);
    return { content: [{ type: "text", text: truncate(result.text) }], details: {} };
  } catch (error) {
    emitBlocked(state, operation, requested, startedAt, error);
    throw error;
  }
}

export function createGuardedReadTools(state: GuardState): ToolDefinition<any, any>[] {
  validateSession(state.session);
  const cwd = state.session.resolved_cwd;
  const readBase = createReadToolDefinition(cwd);
  const grepBase = createGrepToolDefinition(cwd);
  const findBase = createFindToolDefinition(cwd);
  const lsBase = createLsToolDefinition(cwd);

  const read: typeof readBase = {
    ...readBase,
    name: "skuld_read",
    label: "read",
    async execute(_id, params) {
      return runGuarded(state, "read", params.path, async (resolved) => {
        const bytes = await fs.readFile(resolved);
        const text = bytes.toString("utf8");
        const lines = text.split(/\r?\n/);
        const offset = Math.max(1, Math.trunc(params.offset ?? 1));
        const limit = Math.max(1, Math.trunc(params.limit ?? 2000));
        return {
          text: lines.slice(offset - 1, offset - 1 + limit).join("\n"),
          bytesRead: bytes.length,
          contentHash: createHash("sha256").update(bytes).digest("hex"),
        };
      });
    },
  };

  const ls: typeof lsBase = {
    ...lsBase,
    name: "skuld_ls",
    label: "ls",
    async execute(_id, params) {
      const requested = params.path ?? ".";
      return runGuarded(state, "ls", requested, async (resolved) => {
        const entries = await fs.readdir(resolved, { withFileTypes: true });
        const limit = Math.max(1, Math.trunc(params.limit ?? 500));
        const text = entries
          .slice(0, limit)
          .map((entry) => `${entry.isDirectory() ? "d" : "f"} ${entry.name}`)
          .join("\n");
        return { text, bytesRead: 0 };
      });
    },
  };

  const find: typeof findBase = {
    ...findBase,
    name: "skuld_find",
    label: "find",
    async execute(_id, params) {
      const requested = params.path ?? ".";
      return runGuarded(state, "find", requested, async (resolved) => {
        const matcher = globRegex(params.pattern);
        const files = await walkFiles(resolved);
        const limit = Math.max(1, Math.trunc(params.limit ?? 200));
        const matches = files
          .map((file) => path.relative(resolved, file))
          .filter((file) => matcher.test(file) || matcher.test(path.basename(file)))
          .slice(0, limit);
        return { text: matches.join("\n"), bytesRead: 0 };
      });
    },
  };

  const grep: typeof grepBase = {
    ...grepBase,
    name: "skuld_grep",
    label: "grep",
    async execute(_id, params) {
      const requested = params.path ?? ".";
      return runGuarded(state, "grep", requested, async (resolved) => {
        const stat = await fs.stat(resolved);
        const files = stat.isDirectory() ? await walkFiles(resolved) : [resolved];
        const flags = params.ignoreCase ? "i" : "";
        const expression = params.literal
          ? new RegExp(params.pattern.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), flags)
          : new RegExp(params.pattern, flags);
        const fileMatcher = params.glob ? globRegex(params.glob) : undefined;
        const output: string[] = [];
        let bytesRead = 0;
        const digest = createHash("sha256");
        const limit = Math.max(1, Math.trunc(params.limit ?? 200));
        for (const file of files) {
          if (fileMatcher && !fileMatcher.test(path.basename(file))) continue;
          const bytes = await fs.readFile(file);
          bytesRead += bytes.length;
          if (bytesRead > MAX_SCAN_BYTES) throw new Error("SCAN_BYTE_LIMIT_EXCEEDED");
          digest.update(bytes);
          if (bytes.includes(0)) continue;
          const lines = bytes.toString("utf8").split(/\r?\n/);
          for (let index = 0; index < lines.length; index += 1) {
            const line = lines[index] ?? "";
            expression.lastIndex = 0;
            if (expression.test(line)) {
              output.push(`${path.relative(resolved, file)}:${index + 1}:${line}`);
              if (output.length >= limit) break;
            }
          }
          if (output.length >= limit) break;
        }
        return { text: output.join("\n"), bytesRead, contentHash: digest.digest("hex") };
      });
    },
  };

  return [read, grep, find, ls];
}

const execFileAsync = promisify(execFile);

function validateShellCommand(command: string, allowedCommands?: unknown): void {
  if (!command || /[;&|`$<>\r\n]/.test(command)) throw new Error("SHELL_SYNTAX_DENIED");
  const allowed = /^(?:python(?:3(?:\.[0-9]+)?)? -m pytest|pytest|git (?:status|diff|log|branch|switch|add|commit)\b)/;
  const firstPathEntry = (process.env.PATH ?? "").split(path.delimiter)[0] ?? "";
  const runtimePython = firstPathEntry
    ? path.resolve(firstPathEntry, process.platform === "win32" ? "python.exe" : "python")
    : "";
  const pathBoundPytest = runtimePython && command.startsWith(`${runtimePython} -m pytest`);
  if (!allowed.test(command) && !pathBoundPytest) throw new Error("SHELL_COMMAND_NOT_AUTHORIZED");
  if (
    Array.isArray(allowedCommands) &&
    (!allowedCommands.every((value) => typeof value === "string") || !allowedCommands.includes(command))
  ) {
    throw new Error("SHELL_COMMAND_NOT_PLAN_BOUND");
  }
  if (command.split(/[\s=]+/).some((part) => part === ".." || part.startsWith("../"))) {
    throw new Error("TRAVERSAL_DENIED");
  }
}

export function createGuardedMutationTools(state: GuardState): ToolDefinition<any, any>[] {
  validateSession(state.session);
  const cwd = state.session.resolved_cwd;
  const writeBase = createWriteToolDefinition(cwd);
  const editBase = createEditToolDefinition(cwd);
  const bashBase = createBashToolDefinition(cwd);

  const write = {
    ...writeBase,
    name: "skuld_write",
    label: "write",
    async execute(_id: string, params: { path: string; content: string }) {
      return runGuarded(state, "write", params.path, async (resolved) => {
        if (Buffer.byteLength(params.content, "utf8") > 100000) throw new Error("CREATE_BYTE_LIMIT_EXCEEDED");
        await fs.mkdir(path.dirname(resolved), { recursive: true });
        await fs.writeFile(resolved, params.content, { encoding: "utf8", flag: "wx" });
        return { text: `wrote ${Buffer.byteLength(params.content, "utf8")} bytes`, bytesRead: 0 };
      }, false);
    },
  } as unknown as ToolDefinition<any, any>;

  const edit = {
    ...editBase,
    name: "skuld_edit",
    label: "edit",
    async execute(_id: string, params: { path: string; edits: { oldText: string; newText: string }[] }) {
      return runGuarded(state, "edit", params.path, async (resolved) => {
        const before = await fs.readFile(resolved, "utf8");
        let after = before;
        for (const change of params.edits) {
          const first = after.indexOf(change.oldText);
          if (first < 0 || after.indexOf(change.oldText, first + 1) >= 0) {
            throw new Error("EDIT_MATCH_NOT_UNIQUE");
          }
          after = `${after.slice(0, first)}${change.newText}${after.slice(first + change.oldText.length)}`;
        }
        await fs.writeFile(resolved, after, "utf8");
        return { text: `edited ${params.edits.length} region(s)`, bytesRead: 0 };
      });
    },
  } as unknown as ToolDefinition<any, any>;

  const shell = {
    ...bashBase,
    name: "skuld_shell",
    label: "shell",
    async execute(_id: string, params: { command: string; timeout?: number }) {
      const requested = state.currentAction()?.resolved_allowed_targets[0] ?? state.session.resolved_cwd;
      return runGuarded(state, "shell", requested, async (resolved) => {
        validateShellCommand(params.command, state.currentParameters?.()?.allowed_commands);
        let stdout = "";
        let stderr = "";
        let exitCode = 0;
        try {
          const completed = await execFileAsync(
            process.platform === "win32" ? "cmd.exe" : "/bin/sh",
            process.platform === "win32" ? ["/d", "/s", "/c", params.command] : ["-c", params.command],
            { cwd: resolved, timeout: Math.max(1000, Math.min(params.timeout ?? 120000, 300000)), maxBuffer: MAX_TEXT_BYTES },
          );
          stdout = completed.stdout ?? "";
          stderr = completed.stderr ?? "";
        } catch (error) {
          const failed = error as Error & { code?: unknown; stdout?: unknown; stderr?: unknown };
          if (typeof failed.code !== "number" || failed.code < 0) throw error;
          exitCode = failed.code;
          stdout = typeof failed.stdout === "string" ? failed.stdout : "";
          stderr = typeof failed.stderr === "string" ? failed.stderr : "";
        }
        const text = `${stdout ?? ""}${stderr ?? ""}`;
        return {
          text,
          bytesRead: Buffer.byteLength(text, "utf8"),
          contentHash: createHash("sha256").update(text).digest("hex"),
          exitCode,
        };
      });
    },
  } as unknown as ToolDefinition<any, any>;

  return [write, edit, shell];
}

export { canonicalHash, validateSession };
