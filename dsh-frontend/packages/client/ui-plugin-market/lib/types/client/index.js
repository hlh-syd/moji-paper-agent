import { DiscoverSection } from "./DiscoverSection.js";
import { en, ja, es, fr, de, ru, pt, zh } from "./locales.js";
import { DiscoverController } from "./market-store.js";
/** Dictionary namespace owned by this plugin. */
const NS = 'pluginMarket';
/** Services the section reads. */
export const inject = ['slots', 'locale', 'remote', 'remote.pluginMarket', 'remote.pluginRegistryProbe'];
/**
 * Contribute Discover to the Plugins page and keep it current on Host changes.
 * @param ctx - the browser plugin context.
 */
export function apply(ctx) {
    ctx.effect(() => ctx.locale.register(NS, { zh, en, ja, es, fr, de, ru, pt }), 'ui-plugin-market: dictionaries');
    const controller = new DiscoverController(ctx, () => document.baseURI);
    ctx.effect(() => () => { controller.dispose(); }, 'ui-plugin-market: controller');
    ctx.effect(() => {
        const disposers = [
            ctx.remote.$on('plugin-market/changed', () => { controller.sourcesChanged(); }),
            ctx.remote.$on('plugin-manager/changed', () => { controller.bundlesChanged(); }),
            ctx.on('connection/reset', () => {
                controller.sourcesChanged();
                controller.detailsReset();
            }),
        ];
        return () => { for (const dispose of disposers)
            dispose(); };
    }, 'ui-plugin-market: host invalidations');
    const face = controller.face(text => ctx.locale.resolveText(text));
    const t = ctx.locale.bind(NS);
    ctx.slots.inject('plugins.tab', () => ctx.slots.register({
        name: 'plugins.tab', id: 'discover', label: () => t('title'), locale: NS, inject: () => face,
    }, DiscoverSection));
}
//# sourceMappingURL=index.js.map