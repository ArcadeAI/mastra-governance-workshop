/**
 * Sign in as yourself, or as somebody else.
 *
 *     bun run signin
 *
 * Forgets the kept sign-in and runs a new one in your browser, with the workshop username and
 * password from the portal's identity step. Studio's agent reads the same kept sign-in, so run
 * this before `bun run dev`, and again when it expires (an hour).
 */
import { signIn, signOut } from "./src/mastra/identity";

signOut();
try {
  const who = await signIn();
  console.log(`Signed in as ${who.email}, until ${new Date(who.exp * 1000).toLocaleTimeString()}.`);
} catch (err) {
  console.error((err as Error).message);
  process.exit(2);
}
