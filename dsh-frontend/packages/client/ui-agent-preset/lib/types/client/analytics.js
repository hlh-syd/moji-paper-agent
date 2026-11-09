/** Resolve a known selection by preset id without treating an unloaded state as a default.
 * @param id - selected preset id; an empty id means unknown.
 * @returns the built-in category for a shipped id, other for any other id, or undefined until known.
 */
export function analyticsMode(id) {
    if (id === '')
        return undefined;
    switch (id) {
        case 'cordis': return 'creator';
        case 'ptc': return 'ptc';
        case 'minimal': return 'minimal';
        case 'standard': return 'standard';
        default: return 'other';
    }
}
//# sourceMappingURL=analytics.js.map