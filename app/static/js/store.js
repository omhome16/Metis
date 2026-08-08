/* Minimal global state. */

export const state = {
  vaults: [],
  current: null, // current VaultSummary
  theme: "light",
};

export function vaultByName(name) {
  return state.vaults.find((v) => v.name === name) || null;
}
