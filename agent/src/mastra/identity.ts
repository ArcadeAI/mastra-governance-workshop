/**
 * Who the agent acts as: whoever the workshop's identity provider signed in.
 *
 * The portal makes you a user in one of the workshop's Okta orgs (`IDP_ISSUER`), with your own
 * email, and the Loan Desk app there (`IDP_CLIENT_ID`, `IDP_CLIENT_SECRET`) takes this agent's
 * sign-in on `http://127.0.0.1:8765/callback`. `bun run signin` (and `ask` or `tools`, when
 * nobody is signed in) runs authorization code with PKCE in your browser and keeps the ID token in
 * `~/.cache/loan-desk-mastra/` (mode 0600, never printed) until it expires, an hour at Okta's
 * default.
 *
 * Every run reads the kept token and verifies it here: signature against the issuer's key set,
 * issuer, audience and expiry. The verified `email` claim is the Arcade user id every tool call
 * runs as, so the loan desk's `X-Actor`, the policy and the approvals all see the person who
 * signed in. There is no `ARCADE_USER_ID`: a run that cannot verify anyone gets no tools, rather
 * than acting as whoever an `.env` names.
 */
import { createHash, randomBytes } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync, chmodSync, rmSync } from "node:fs";
import { createServer } from "node:http";
import { homedir } from "node:os";
import { join } from "node:path";
import { createRemoteJWKSet, jwtVerify } from "jose";

/** The loopback callback the portal registers in every Okta org's Loan Desk app. */
const CALLBACK = { host: "127.0.0.1", port: 8765 };
const REDIRECT = `http://${CALLBACK.host}:${CALLBACK.port}/callback`;

/** The person a verified ID token names. */
export interface Who {
  email: string;
  name?: string;
  /** Expiry, in seconds since the epoch. */
  exp: number;
}

const env = (name: string) => process.env[name]?.trim() || "";

/** The issuer, without a trailing slash, and the client id: enough to verify. */
function config() {
  return { issuer: env("IDP_ISSUER").replace(/\/+$/, ""), clientId: env("IDP_CLIENT_ID") };
}

/** The identity values `.env` is missing, by name. Empty when the agent can verify a sign-in. */
export function missingIdentity(withSecret = false): string[] {
  const names = ["IDP_ISSUER", "IDP_CLIENT_ID", ...(withSecret ? ["IDP_CLIENT_SECRET"] : [])];
  return names.filter((n) => !env(n));
}

interface Discovery {
  issuer: string;
  authorization_endpoint: string;
  token_endpoint: string;
  jwks_uri: string;
}

const discoveries = new Map<string, Promise<Discovery>>();
/** The issuer's discovery document, once per process, checked to name this issuer as its own. */
function discover(issuer: string): Promise<Discovery> {
  let d = discoveries.get(issuer);
  if (!d) {
    d = (async () => {
      const url = `${issuer}/.well-known/openid-configuration`;
      const res = await fetch(url, { signal: AbortSignal.timeout(30_000) });
      if (!res.ok) throw new Error(`${url} answered ${res.status}`);
      const doc = (await res.json()) as Partial<Discovery>;
      if (doc.issuer !== issuer) throw new Error(`${url} names the issuer ${doc.issuer}, not ${issuer}`);
      const missing = (["authorization_endpoint", "token_endpoint", "jwks_uri"] as const).filter((k) => !doc[k]);
      if (missing.length) throw new Error(`${url} has no ${missing.join(", ")}`);
      return doc as Discovery;
    })();
    d.catch(() => discoveries.delete(issuer));
    discoveries.set(issuer, d);
  }
  return d;
}

const keySets = new Map<string, ReturnType<typeof createRemoteJWKSet>>();
const keySet = (uri: string) => {
  if (!keySets.has(uri)) keySets.set(uri, createRemoteJWKSet(new URL(uri)));
  return keySets.get(uri)!;
};

/** Verifies an ID token against the configured issuer and client, and returns who it names. Throws when it does not hold. */
export async function verify(token: string): Promise<Who> {
  const { issuer, clientId } = config();
  const d = await discover(issuer);
  const { payload } = await jwtVerify(token, keySet(d.jwks_uri), { issuer, audience: clientId, algorithms: ["RS256"] });
  const email = typeof payload.email === "string" ? payload.email : "";
  if (!email) throw new Error("The ID token carries no email claim; the sign-in must ask for the email scope.");
  return { email, name: typeof payload.name === "string" ? payload.name : undefined, exp: payload.exp ?? 0 };
}

function sessionPath() {
  const { issuer, clientId } = config();
  const base = process.env.XDG_CACHE_HOME?.trim() || join(homedir(), ".cache");
  return join(base, "loan-desk-mastra", `${createHash("sha256").update(`${issuer}\n${clientId}`).digest("hex").slice(0, 16)}.json`);
}

/** The person signed in now: the kept ID token, verified and good for another minute, or null. */
export async function signedIn(): Promise<Who | null> {
  if (missingIdentity().length) return null;
  let token: string;
  try {
    token = JSON.parse(readFileSync(sessionPath(), "utf8")).id_token;
  } catch {
    return null;
  }
  try {
    const who = await verify(token);
    return who.exp - Date.now() / 1000 > 60 ? who : null;
  } catch {
    return null;
  }
}

/** Throws the kept sign-in away. */
export function signOut() {
  rmSync(sessionPath(), { force: true });
}

const b64url = (b: Buffer) => b.toString("base64url");

/**
 * Signs in in the browser (authorization code with PKCE), keeps the ID token and returns who it
 * names. `prompt=login` always asks for the password, so a browser left signed in as somebody
 * else never answers for you.
 */
export async function signIn(): Promise<Who> {
  const missing = missingIdentity(true);
  if (missing.length) throw new Error(`Not set: ${missing.join(", ")}. The portal's Start your agent step shows them.`);
  const { issuer, clientId } = config();
  const d = await discover(issuer);
  const verifier = b64url(randomBytes(48));
  const challenge = b64url(createHash("sha256").update(verifier).digest());
  const state = b64url(randomBytes(16));
  const url = `${d.authorization_endpoint}?${new URLSearchParams({
    response_type: "code",
    client_id: clientId,
    redirect_uri: REDIRECT,
    scope: "openid email profile",
    prompt: "login",
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
  })}`;

  const code = await catchCode(state, url);
  // Okta's Loan Desk app takes the client secret over HTTP Basic (client_secret_basic).
  const basic = Buffer.from(`${encodeURIComponent(clientId)}:${encodeURIComponent(env("IDP_CLIENT_SECRET"))}`).toString("base64");
  const res = await fetch(d.token_endpoint, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded", authorization: `Basic ${basic}` },
    body: new URLSearchParams({ grant_type: "authorization_code", code, redirect_uri: REDIRECT, code_verifier: verifier }),
    signal: AbortSignal.timeout(30_000),
  });
  const body = (await res.json().catch(() => ({}))) as { id_token?: string; error?: string; error_description?: string };
  if (!res.ok || !body.id_token) throw new Error(`The token exchange failed: ${body.error_description ?? body.error ?? res.status}`);
  const who = await verify(body.id_token);

  const path = sessionPath();
  mkdirSync(join(path, ".."), { recursive: true });
  writeFileSync(path, JSON.stringify({ id_token: body.id_token }), { mode: 0o600 });
  chmodSync(path, 0o600);
  return who;
}

/** Serves the callback on loopback until it brings this sign-in's code, opening the browser on the way. */
function catchCode(state: string, url: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const server = createServer((req, res) => {
      const q = new URL(req.url ?? "/", REDIRECT).searchParams;
      const done = (status: number, text: string) => {
        res.writeHead(status, { "content-type": "text/plain; charset=utf-8" }).end(text);
      };
      if (!req.url?.startsWith("/callback")) return done(404, "Not found.");
      if (q.get("state") !== state) return done(400, "This sign-in was not the one the agent started.");
      server.close();
      clearTimeout(timer);
      const code = q.get("code");
      if (!code) {
        done(400, "The sign-in came back without a code. Go back to the terminal.");
        return reject(new Error(`The identity provider answered ${q.get("error") ?? "no code"}: ${q.get("error_description") ?? ""}`.trim()));
      }
      done(200, "Signed in. You can close this tab and go back to the terminal.");
      resolve(code);
    });
    const timer = setTimeout(() => {
      server.close();
      reject(new Error("Nobody signed in within five minutes."));
    }, 5 * 60_000);
    server.on("error", (err) => {
      clearTimeout(timer);
      reject(new Error(`Could not listen on ${REDIRECT} (${err.message}). Is another sign-in still waiting?`));
    });
    server.listen(CALLBACK.port, CALLBACK.host, () => {
      console.error(`Sign in as yourself, with the workshop username and password from the identity step:\n  ${url}\n`);
      const opener = process.platform === "darwin" ? "open" : process.platform === "win32" ? "explorer" : "xdg-open";
      import("node:child_process").then(({ spawn }) => spawn(opener, [url], { stdio: "ignore", detached: true }).on("error", () => undefined).unref());
    });
  });
}
