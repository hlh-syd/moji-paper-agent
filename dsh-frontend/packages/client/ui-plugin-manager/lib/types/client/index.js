import { PluginManagerPage } from "./PluginManagerPage.js";
import { createPluginHeaderRef } from "./header-scroll.js";
import { PluginRefreshToast } from "./PluginRefreshToast.js";
import { PluginsPanelIcon } from "./PluginsPanelIcon.js";
import { configLedgerSource } from "./config-ledger.js";
import { tabLedgerSource } from "./tab-ledger.js";
import { PluginManagerController } from "./manager-store.js";
import { en, ja, es, fr, de, ru, pt, zh } from "./locales.js";
import { createNavigationStore } from "./navigation-store.js";
/** Dictionary namespace owned by this plugin. */
export const NS = 'pluginManager';
/** The id shared by the sidebar entry and the main panel it opens. */
export const PANEL_ID = 'plugins';
/** Services required by the sidebar registration, the Remote methods, and access; the inventory says whether the Host manages a profile. */
export const inject = ['slots', 'locale', 'remote', 'remote.pluginManager', 'remote.pluginInventory', 'remote.pluginRegistryProbe', 'configForms', 'layout', 'remoteAccess'];
/**
 * Contribute the Plugins entry to the sidebar with the management page it
 * opens, and keep it current on the Host's change events, while the current
 * Peer may list the installed bundles.
 * @param ctx - the browser plugin context.
 */
export function apply(ctx) {
    ctx.effect(() => ctx.locale.register(NS, { zh, en, ja, es, fr, de, ru, pt }), 'ui-plugin-manager: dictionaries');
    const t = ctx.locale.bind(NS);
    const controller = new PluginManagerController(ctx);
    ctx.effect(() => () => { controller.dispose(); }, 'ui-plugin-manager: controller');
    ctx.provide('pluginChanges', { bundle: packageName => controller.observeChanges(packageName) });
    // The page is a global panel: it belongs to the profile, not to a Session,
    // and the sidebar's entry selects it. What is installed and switched on is
    // the page's own; a plugin's configuration arrives through the slots the
    // page declares here, so the page never names a configurable plugin.
    const configLedger = configLedgerSource(ctx);
    const face = controller.inject(configLedger, tabLedgerSource(ctx), text => ctx.locale.resolveText(text));
    ctx.remoteAccess.whenAccess('pluginManager/listBundles', function* () {
        // The Host says when what is installed, enabled, or composed changed — from
        // this page, the CLI, or another browser — and streams install output.
        // A page never rendered holds no snapshot to refresh.
        const refresh = () => {
            // A profile change can replace a bundle's files, and a restarted Host is read afresh.
            controller.details.reset();
            if (controller.getSnapshot().status !== 'idle')
                void controller.load();
        };
        // Host changes made while access was withdrawn reached no subscription below.
        refresh();
        yield ctx.remote.$on('plugin-manager/changed', refresh);
        yield ctx.remote.$on('plugin-manager/install-log', (chunk) => { controller.appendLog(chunk); });
        yield ctx.remote.$on('plugin-manager/install-state', (progress) => { controller.installProgress(progress); });
        yield ctx.remote.$on('plugin-manager/update-state', (progress) => { controller.updateProgress(progress); });
        yield ctx.on('connection/reset', refresh);
        yield ctx.slots.inject('shell.overlay', () => ctx.slots.register({
            name: 'shell.overlay', id: 'plugin-manager.refresh-toast', locale: NS,
            inject: () => ({
                hooks: { pluginManager: face.hooks.pluginManager },
                dismissNotice: face.dismissNotice,
            }),
        }, PluginRefreshToast));
        yield ctx.slots.inject('main', function* () {
            const handle = createNavigationStore(), instance = handle.create();
            const store = { ...handle, create: () => instance };
            yield ctx.slots.register({
                name: 'main',
                key: PANEL_ID,
                locale: NS,
                store,
                inject: () => ({ ...face, createHeaderRef: createPluginHeaderRef }),
                children: {
                    'plugins.add.actions': { kind: 'list', scope: 'root' },
                    'plugins.tab': { kind: 'list', scope: 'root' },
                    'plugins.item': { kind: 'list', scope: 'root' },
                    'plugins.bundle.activation': { kind: 'keyed', scope: 'root' },
                    'plugins.bundle.config': { kind: 'keyed', scope: 'root' },
                    'plugins.row.config': { kind: 'keyed', scope: 'root' },
                    'plugins.detail.actions': { kind: 'list', scope: 'root' },
                    'plugins.detail.badge': { kind: 'list', scope: 'root' },
                    'plugins.detail.section': { kind: 'list', scope: 'root' },
                },
            }, PluginManagerPage);
            yield ctx.layout.panelInfo.subscribe(() => {
                if (ctx.layout.panelInfo.getSnapshot().activePanelId !== PANEL_ID)
                    instance.actions.setView({ kind: 'list' });
            });
            const disposeNavigation = ctx.reflect.provide('pluginNavigation', {
                openList: () => {
                    ctx.layout.selectPanel(PANEL_ID);
                    instance.actions.setTab('installed');
                    instance.actions.setView({ kind: 'list' });
                },
                openBundle: (packageName) => {
                    ctx.layout.selectPanel(PANEL_ID);
                    instance.actions.setTab('installed');
                    instance.actions.setView({ kind: 'package', name: packageName });
                },
            });
            yield () => { void disposeNavigation(); };
        });
        yield ctx.slots.inject('sidebar.panellist', () => ctx.slots.register({
            name: 'sidebar.panellist',
            id: PANEL_ID,
            order: 0,
            label: () => t('panel'),
            locale: NS,
        }, PluginsPanelIcon));
    });
}
//# sourceMappingURL=index.js.map