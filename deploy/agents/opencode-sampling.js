// OpenCode plugin (Deck's OpenCode config loads it, core.env_prefix): Settings → OpenCode sampling. Each request of
// the plan or build agent gets that agent's temperature, top_p and top_k, read fresh from Deck's settings, so a change
// applies without a restart. Skipped where OpenCode's model list says the model takes no temperature (reasoning
// models), and for models in the skip list: the chat bridge adds a model there when it rejects them anyway.
import { readFileSync } from "node:fs"

const read = (file, empty) => {
  try {
    return JSON.parse(readFileSync(file, "utf8"))
  } catch {
    return empty
  }
}

export const DeckSampling = async () => ({
  "chat.params": async (input, output) => {
    const settings = read(process.env.DECK_SAMPLING, {})
    // Shared routing covers every OpenRouter model and agent, including models that reject sampling.
    if (input.model.providerID === "openrouter" && settings.openrouter) {
      output.options ||= {}
      output.options.provider = { ...output.options.provider, ...settings.openrouter }
    }
    const s = (settings.sampling || {})[input.agent]
    if (!s || !input.model.capabilities?.temperature) return
    if (read(process.env.DECK_SAMPLING_SKIP, []).includes(`${input.model.providerID}/${input.model.id}`)) return
    if (s.temperature != null) output.temperature = s.temperature
    if (s.top_p != null) output.topP = s.top_p
    if (s.top_k != null) output.topK = s.top_k
  },
})
