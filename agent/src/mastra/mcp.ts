/**
 * The one connection to your Arcade MCP gateway, signed in as you.
 *
 * `ARCADE_MCP_URL` is the gateway the portal made in your project. The gateway is an
 * OAuth-protected MCP server: the first time this client connects it registers itself as a
 * public client, opens the workshop identity provider's sign-in in your browser, and catches
 * the code on a loopback port (RFC 8252). The tokens are kept in `.tokens.json` next to this
 * file, so every `bun run ask` after the first reuses the sign-in until it expires. Delete the
 * file to sign in again, as somebody else or for a new gateway.
 *
 * Every tool call then carries your identity: Arcade runs it as you, and your policy (the
 * portal's Policies step) is checked on each one. Nothing in this file names a tool: the gateway
 * decides what you can see.
 */
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { spawn } from "node:child_process";
import { MCPClient, MCPOAuthClientProvider, type OAuthStorage } from "@mastra/mcp";

type ProviderOptions = ConstructorParameters<typeof MCPOAuthClientProvider>[0];
type ClientInformation = ReturnType<MCPOAuthClientProvider["clientInformation"]>;

/** The gateway's key in the client: tool names come back as `arcade_<Tool>`. */
export const SERVER = "arcade";

/** The kept sign-in: `agent/.tokens.json`, next to package.json, wherever the agent runs from (bun scripts and `mastra dev` both start there). */
const TOKENS = process.env.TOKENS_FILE?.trim() || join(process.cwd(), ".tokens.json");

/** The provider's storage, as one JSON file this user can read, written whole on every change. */
class FileStorage implements OAuthStorage {
  private data: Record<string, string>;
  constructor(private readonly file: string) {
    this.data = existsSync(file) ? (JSON.parse(readFileSync(file, "utf8")) as Record<string, string>) : {};
  }
  get(key: string) {
    return this.data[key];
  }
  set(key: string, value: string) {
    this.data[key] = value;
    this.flush();
  }
  delete(key: string) {
    delete this.data[key];
    this.flush();
  }
  clear() {
    this.data = {};
    this.flush();
  }
  private flush() {
    mkdirSync(dirname(this.file), { recursive: true });
    const tmp = this.file + ".tmp";
    writeFileSync(tmp, JSON.stringify(this.data, null, 2), { mode: 0o600 });
    renameSync(tmp, this.file);
  }
}

/** Where the gateway's answer to our registration is kept, in the same file as the tokens. */
const REGISTRATION = "client_registration";

/**
 * Mastra's provider wants a client identity up front: a pre-registered client, or a metadata
 * document on a public https address. Arcade also takes dynamic registration (RFC 7591), the
 * way Claude Code and Cursor sign in to a gateway, and that needs nothing hosted: the MCP SDK
 * registers this agent as a public client the first time it connects, when `clientInformation`
 * comes back empty and `saveClientInformation` exists. The registration is kept with the tokens.
 */
class RegisteringProvider extends MCPOAuthClientProvider {
  constructor(options: Omit<ProviderOptions, "clientInformation" | "clientMetadataUrl" | "storage">, private readonly store: FileStorage) {
    super({ ...options, storage: store, clientInformation: { client_id: "unregistered" } });
  }
  override clientInformation(): ClientInformation {
    const raw = this.store.get(REGISTRATION);
    // Empty until the first sign-in: the SDK then registers. The base type does not allow undefined, the SDK's does.
    return (raw ? JSON.parse(raw) : undefined) as ClientInformation;
  }
  saveClientInformation(info: ClientInformation) {
    this.store.set(REGISTRATION, JSON.stringify(info));
  }
  override async invalidateCredentials(scope: Parameters<MCPOAuthClientProvider["invalidateCredentials"]>[0]) {
    await super.invalidateCredentials(scope);
    if (scope === "all" || scope === "client") this.store.delete(REGISTRATION);
  }
}

export interface Gateway {
  client: MCPClient;
  /** Signs in if the gateway asks for it, then returns. Opens the browser at most once per run. */
  ready(): Promise<void>;
  /** The authorization links the gateway asked for during a run (Arcade needing your permission to use Slack, for example). */
  links: string[];
}

let current: Gateway | null | undefined;

/** The gateway from `ARCADE_MCP_URL`, one per process, or null when it is not set: the agent then has no tools. */
export function gateway(): Gateway | null {
  if (current !== undefined) return current;
  current = makeGateway();
  return current;
}

function makeGateway(): Gateway | null {
  const url = process.env.ARCADE_MCP_URL?.trim();
  if (!url) return null;
  const port = Number(process.env.OAUTH_CALLBACK_PORT ?? 8765);
  // The literal loopback address, so the browser and the callback server agree on the address family.
  const redirectUrl = `http://127.0.0.1:${port}/oauth/callback`;
  const links: string[] = [];
  // The probe in ready() runs the SDK's auth flow far enough to learn the state; its sign-in link is not the real one
  // (no listener is up yet), so it stays unprinted. authenticate() then runs the flow with the loopback server listening.
  let probing = false;

  const authProvider = new RegisteringProvider({
    redirectUrl,
    clientMetadata: {
      client_name: "Mastra governance workshop agent",
      redirect_uris: [redirectUrl],
      grant_types: ["authorization_code", "refresh_token"],
      response_types: ["code"],
      token_endpoint_auth_method: "none",
      scope: "mcp offline_access",
    },
    onRedirectToAuthorization: async (authUrl) => {
      if (probing) return;
      console.error(`\nSign in to your gateway as your workshop identity. If the browser did not open, go to:\n\n  ${authUrl}\n`);
      // `open` is macOS; Linux and Windows attendees copy the printed address.
      try {
        spawn("open", [authUrl.toString()], { stdio: "ignore", detached: true }).on("error", () => undefined).unref();
      } catch {
        /* printed above */
      }
    },
  }, new FileStorage(TOKENS));

  const client = new MCPClient({
    id: "mastra-governance-workshop",
    servers: {
      [SERVER]: {
        url: new URL(url),
        authProvider,
        // Arcade asks for permissions it does not have yet (Slack, the first time) as a URL
        // elicitation. The link is printed and the call is left unanswered, so the model
        // tells you to open it and ask again.
        capabilities: { elicitation: { url: {} } },
        inputRequests: async ({ params }) => {
          const p = params as { mode?: string; url?: string; message?: string };
          if (p.mode === "url" && p.url) {
            links.push(p.url);
            console.error(`\nArcade needs your permission first${p.message ? `: ${p.message}` : ""}\n\n  ${p.url}\n`);
          }
          return { action: "cancel" };
        },
        // A refused call (your policy saying no) reaches the model as the error it is, with the reason.
        onToolError: "throw",
      },
    },
  });

  return {
    client,
    links,
    async ready() {
      // An unauthenticated client lists no tools and raises nothing, so the auth state is checked first.
      probing = true;
      try {
        await client.listTools().catch(() => ({}));
      } finally {
        probing = false;
      }
      if (client.getServerAuthState(SERVER) === "needs-auth") await client.authenticate(SERVER, { timeoutMs: 5 * 60_000 });
    },
  };
}

/**
 * The gateway's tools for the agent, signed in as you. Called by the agent on every run (Mastra resolves
 * dynamic tools per request), so a sign-in that expired is renewed, and Studio's chat triggers the first
 * sign-in in your browser the same way `bun run tools` does.
 */
export async function gatewayTools(): Promise<Record<string, unknown>> {
  const gw = gateway();
  if (!gw) return {};
  await gw.ready();
  return gw.client.listTools();
}
