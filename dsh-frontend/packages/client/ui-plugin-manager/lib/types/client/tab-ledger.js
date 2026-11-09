/** The tabs other plugins contribute to the Plugins page through `plugins.tab`, in the active locale. */
import { resolveSlotLabel } from '@deepseek-ai/dsh-client-ui-slots';
/**
 * Project the `plugins.tab` ledger as one observable the page binds.
 * @param ctx - the page plugin's context, whose slot registry and locale the projection follows.
 * @returns the tab source; its snapshot changes only when the ledger or the locale does.
 */
export function tabLedgerSource(ctx) {
    let version = -1;
    let revision = -1;
    let tabs = [];
    return {
        getSnapshot: () => {
            const next = ctx.slots.getVersion('plugins.tab');
            const current = ctx.locale.getSnapshot().revision;
            if (next !== version || current !== revision) {
                version = next;
                revision = current;
                tabs = ctx.slots.entries('plugins.tab').map(entry => ({
                    /* v8 ignore next -- list-slot registration requires id */
                    id: entry.options.id ?? '',
                    label: resolveSlotLabel(entry.options.label) ?? '',
                }));
            }
            return tabs;
        },
        subscribe: (listener) => {
            const offs = [ctx.slots.subscribe('plugins.tab', listener), ctx.locale.subscribe(listener)];
            return () => { for (const off of offs)
                off(); };
        },
    };
}
//# sourceMappingURL=tab-ledger.js.map