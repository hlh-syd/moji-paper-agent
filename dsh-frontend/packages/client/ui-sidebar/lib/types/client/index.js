import { createSnapshotStore } from '@deepseek-ai/dsh-client-store';
import { resolveSlotLabel } from '@deepseek-ai/dsh-client-ui-slots';
import { createPanelPins } from "./panel-pins.js";
import { HeaderLeadingControls } from "./HeaderLeadingControls.js";
import { SidebarRoot } from "./SidebarRoot.js";
import { en, ja, es, fr, de, ru, pt, zh } from "./locales.js";
/** Dictionary namespace owned by this plugin. */
const NS = 'sidebar';
/** Services required by the sidebar plugin. */
export const inject = ['slots', 'layout', 'uiWorkspace', 'locale', 'shortcuts'];
/** Registers the sidebar shell and its service callbacks.
 * @param ctx - Client root context.
 */
export function apply(ctx) {
    const workspaceNavigation = ctx.get('uiWorkspace');
    ctx.effect(() => ctx.locale.register(NS, { zh, en, ja, es, fr, de, ru, pt }), 'ui-sidebar: dictionaries');
    const panels = createSnapshotStore([]);
    const syncPanels = () => {
        const next = ctx.slots.entriesOfSlot('sidebar.panellist').map(({ options }) => {
            // The list registration requires an id; StoredEntry erases the slot kind.
            const id = options.id;
            return { id, order: options.order ?? 0, label: resolveSlotLabel(options.label) ?? id };
        }).sort((a, b) => a.order - b.order);
        const previous = panels.getSnapshot();
        if (previous.length === next.length && previous.every((panel, index) => {
            const candidate = next[index];
            return panel.id === candidate.id && panel.order === candidate.order && panel.label === candidate.label;
        }))
            return;
        panels.set(next);
    };
    ctx.effect(() => ctx.slots.subscribe('sidebar.panellist', syncPanels), 'ui-sidebar: panel entries');
    ctx.effect(() => ctx.locale.subscribe(syncPanels), 'ui-sidebar: panel labels');
    const panelPins = createPanelPins();
    const entryActions = {
        getSnapshot: () => ctx.slots.entriesOfSlot('sidebar.panellist.actions').length > 0,
        subscribe: (listener) => ctx.slots.subscribe('sidebar.panellist.actions', listener),
    };
    const injectProps = () => ({
        // The shell's New Session button rides the Workspace UI's shared action
        // (current Session Workspace, then recent Workspace).
        startSession: (workspaceId) => { workspaceNavigation.startSession(workspaceId); },
        toggleSidebar: () => { ctx.layout.toggleSidebar(); },
        selectPanel: (id) => { ctx.layout.selectPanel(id); },
        setPinned: panelPins.setPinned,
        trackEntryClick: (attributes) => { ctx.get('productAnalytics')?.track('sidebar_menu_click', attributes); },
        hooks: { panels, shortcuts: ctx.shortcuts.catalog, entryPins: panelPins.unpinned, entryActions },
    });
    ctx.slots.inject('sidebar', () => ctx.slots.register({
        name: 'sidebar',
        locale: NS,
        children: {
            'sidebar.brand.mark': { kind: 'single', scope: 'root' },
            'sidebar.brand.name': { kind: 'single', scope: 'root' },
            'sidebar.toggle.badge': { kind: 'single', scope: 'root' },
            'sidebar.panellist': { kind: 'list', scope: 'root' },
            'sidebar.panellist.actions': { kind: 'list', scope: 'root' },
            'sidebar.workspaces': { kind: 'single', scope: 'root' },
            'sidebar.settings': { kind: 'single', scope: 'root' },
            'sidebar.footer.action': { kind: 'list', scope: 'root' },
        },
        inject: injectProps,
    }, SidebarRoot));
    // macOS desktop hides the collapsed sidebar entirely, so the open/New
    // Session controls move into the frame's window-chrome seat beside the
    // traffic lights; the occupant reuses the shell's injected actions, and
    // the AppFrame mounts the seat only while the column is fully hidden.
    ctx.slots.inject('shell.leading', () => ctx.slots.register({
        name: 'shell.leading',
        locale: NS,
        inject: injectProps,
    }, HeaderLeadingControls));
    syncPanels();
}
//# sourceMappingURL=index.js.map