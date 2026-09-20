/* Minimal global state. */

export const state = {
  vaults: [],
  current: null, // current VaultSummary
  theme: "light",
  user: null, // { email, display_name } when signed in (users mode)
  needsAuth: false, // users mode active and not signed in
};

export function vaultByName(name) {
  return state.vaults.find((v) => v.name === name) || null;
}
