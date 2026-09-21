import { segment, type RequestOptions, type Transport } from "../http.js";
import type { Agent, AgentCreate, AgentDeleted, AgentUpdate } from "../types.js";

/** `/v1/agents`. Every call is scoped to the API key's organization. */
export class Agents {
  readonly #http: Transport;

  constructor(http: Transport) {
    this.#http = http;
  }

  /** `POST /v1/agents`. */
  create(body: AgentCreate, options?: RequestOptions): Promise<Agent> {
    return this.#http.request("POST", "/v1/agents", body, options);
  }

  /** `GET /v1/agents`: every agent of the organization. The API is not paginated. */
  list(options?: RequestOptions): Promise<Agent[]> {
    return this.#http.request("GET", "/v1/agents", undefined, options);
  }

  /** `GET /v1/agents/{id}`. Another organization's agent is a 404, like a missing one. */
  get(agentId: string, options?: RequestOptions): Promise<Agent> {
    return this.#http.request("GET", `/v1/agents/${segment(agentId)}`, undefined, options);
  }

  /** `PATCH /v1/agents/{id}`: only the fields you send change. */
  update(agentId: string, body: AgentUpdate, options?: RequestOptions): Promise<Agent> {
    return this.#http.request("PATCH", `/v1/agents/${segment(agentId)}`, body, options);
  }

  /** `DELETE /v1/agents/{id}`: permanent. There is no archive in the developer API. */
  delete(agentId: string, options?: RequestOptions): Promise<AgentDeleted> {
    return this.#http.request("DELETE", `/v1/agents/${segment(agentId)}`, undefined, options);
  }
}
