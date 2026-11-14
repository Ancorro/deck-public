// Deck loads this into Pi sessions (pi -e): an unnamed session takes its name from the first message, so Pi's
// terminal title ("π - <name> - <folder>") gives the Deck tab a name, like the task name Claude sets.
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

// A chat message starts with Deck's stamp ("[Tue 2026-10-13 18:50 -0700 · phone app] "): not part of the name
const STAMP = /^\[[A-Z][a-z]{2} \d{4}-\d\d-\d\d \d\d:\d\d [+-]\d{4}[^\]]*\]\s*/;

export function nameFrom(text: string): string {
	const line = text.trim().replace(STAMP, "").split("\n")[0]!.replace(/\s+/g, " ").trim();
	if (line.length <= 40) return line;
	const cut = line.slice(0, 40);
	return (cut.lastIndexOf(" ") > 20 ? cut.slice(0, cut.lastIndexOf(" ")) : cut) + "…";
}

export default function (pi: ExtensionAPI) {
	pi.on("input", (event) => {
		const text = event.text.trim();
		if (text && !text.startsWith("/") && !pi.getSessionName()) pi.setSessionName(nameFrom(text));
		return { action: "continue" };
	});
}
