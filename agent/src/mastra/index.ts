/**
 * The Mastra instance: the agent, where runs are kept, and where traces go.
 *
 * Studio (`bun run dev`, http://localhost:4111) serves this instance; `bun run ask` uses it directly. Either way
 * every run is traced. Traces always land in the local `mastra.db` (Studio reads them from there). With
 * `MASTRA_PLATFORM_ACCESS_TOKEN` and `MASTRA_PROJECT_ID` in `.env`, from your Mastra platform project, they go
 * there as well: that is the workshop's take-home view of what your agent did and what your policy refused.
 */
import { Mastra } from "@mastra/core/mastra";
import { LibSQLStore } from "@mastra/libsql";
import { MastraPlatformExporter, MastraStorageExporter, Observability } from "@mastra/observability";
import { loanDesk } from "./agents/loan-desk";

const platform = Boolean(process.env.MASTRA_PLATFORM_ACCESS_TOKEN?.trim() && process.env.MASTRA_PROJECT_ID?.trim());

export const mastra = new Mastra({
  agents: { loanDesk },
  storage: new LibSQLStore({ id: "local", url: process.env.MASTRA_DB_URL?.trim() || "file:./mastra.db" }),
  observability: new Observability({
    configs: {
      default: {
        serviceName: "loan-desk",
        exporters: [new MastraStorageExporter(), ...(platform ? [new MastraPlatformExporter()] : [])],
      },
    },
  }),
});

/** Whether traces also go to the Mastra platform this run. */
export const platformTracing = platform;
