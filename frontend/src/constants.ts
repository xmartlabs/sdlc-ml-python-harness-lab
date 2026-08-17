// Example endpoints shown in Settings help text.
//
// Mirrors src/harness_lab_app/constants.py. Two strings do not justify an
// endpoint round-trip, but they do need one definition per side: the copies
// they replaced had drifted into a specific developer's LAN addresses.
//
// RFC-1918 and deliberately generic — the example exists to show the *shape*
// (host + port + the /v1 suffix people forget), not a reachable server.

export const EXAMPLE_LMSTUDIO_URL = "http://192.168.1.50:1234/v1";
export const EXAMPLE_OLLAMA_URL = "http://192.168.1.50:11434/v1";
