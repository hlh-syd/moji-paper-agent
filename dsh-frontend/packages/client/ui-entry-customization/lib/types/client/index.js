import { createSnapshotStore } from '@deepseek-ai/dsh-client-store';
import { EntryMenuContent } from "./EntryMenuContent.js";
import { en, ja, es, fr, de, ru, pt, zh } from "./locales.js";
/** Services used by the menu rows' footer actions; the optional `pluginNavigation` shows the discovery row while present. */
export const inject = ['slots', 'locale', 'uiWorkspace'];
/**
 * Register the same menu rows into each region's More menu.
 * @param ctx - Browser context with slot, locale, and Workspace services, and the optional plugin navigation.
 */
export function apply(ctx) {
    ctx.effect(() => ctx.locale.register('entryCustomization', { en, zh, ja, es, fr, de, ru, pt }));
    const t = ctx.locale.bind('entryCustomization');
    // The Plugins page provides its navigation only while the current Peer may use it.
    const pluginNavigation = createSnapshotStore(false);
    ctx.inject(['pluginNavigation'], (scope) => {
        scope.effect(() => {
            pluginNavigation.set(true);
            return () => { pluginNavigation.set(false); };
        }, 'entry-customization: plugin navigation presence');
    });
    const injected = (region) => ({
        region,
        hooks: { pluginNavigation },
        browsePlugins: () => {
            ctx.get('productAnalytics')?.track('more_menu_item_click', { region, action: 'browse_plugins' });
            ctx.get('pluginNavigation')?.openList();
        },
        createPlugin: () => {
            ctx.get('productAnalytics')?.track('more_menu_item_click', { region, action: 'create_plugin' });
            ctx.uiWorkspace.startSession(undefined, {
                agentPreset: 'cordis', prompt: t(`createPrompt.${region}`), whenDraftExists: 'append',
            });
        },
    });
    ctx.slots.inject('sidebar.panellist.actions', () => ctx.slots.register({
        name: 'sidebar.panellist.actions', id: 'entry-customization', locale: 'entryCustomization',
        inject: () => injected('sidebar'),
    }, EntryMenuContent));
    ctx.slots.inject('conversation.session.header.views.actions', () => ctx.slots.register({
        name: 'conversation.session.header.views.actions', id: 'entry-customization', locale: 'entryCustomization',
        inject: () => injected('conversation'),
    }, EntryMenuContent));
    ctx.slots.inject('sidebar.right.tab.guide.actions', () => ctx.slots.register({
        name: 'sidebar.right.tab.guide.actions', id: 'entry-customization', locale: 'entryCustomization',
        inject: () => injected('rightbar'),
    }, EntryMenuContent));
}
//# sourceMappingURL=index.js.map