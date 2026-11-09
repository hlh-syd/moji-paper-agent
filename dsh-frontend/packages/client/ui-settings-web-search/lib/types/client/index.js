/**
 * The web-search settings page, browser half: the switch between DeepSeek
 * Messages search and the Search and Fetch APIs, which writes the `web`
 * provider selection, and the selected API's key, endpoint, and per-search
 * limit over the `web-search-deepseek` or `web-deepseek-api` namespace. The
 * page registers into the Plugins page's `plugins.item` slot while the Host
 * serves `web-search-deepseek`, so a deployment without that provider shows no
 * trace of it; the switch appears only while the Host also serves `web` and
 * `web-deepseek-api`.
 */
import { WebSearchCard } from "./WebSearchCard.js";
import { WEB_API_NS, WEB_NS, WEB_SEARCH_NS, WebSearchCardController } from "./web-search-card-controller.js";
import { en, ja, es, fr, de, ru, pt, zh } from "./locales.js";
/** Dictionary namespace owned by this plugin. */
export const NS = 'settings.webSearch';
/** Required services (cordis fiber inject). */
export const inject = ['slots', 'locale', 'remote', 'remote.credentials', 'configForms'];
/**
 * Mount the web-search settings page while the Host serves its namespace.
 * @param ctx - the browser plugin context.
 */
export function apply(ctx) {
    const t = ctx.locale.bind(NS);
    ctx.effect(() => ctx.locale.register(NS, { zh, en, ja, es, fr, de, ru, pt }), 'ui-settings-web-search: dictionaries');
    const card = new WebSearchCardController({
        web: ctx.configForms.get(WEB_NS), messages: ctx.configForms.get(WEB_SEARCH_NS), api: ctx.configForms.get(WEB_API_NS),
    }, ctx);
    ctx.effect(() => () => { card.dispose(); }, 'ui-settings-web-search: form subscription');
    // The credential the page reports is not part of any settings section, so
    // no scope publishes when one is written. This is the only signal
    // that a key written on another surface reached the Host.
    ctx.effect(() => ctx.remote.$on('credentials/reference-updated', (ref) => { card.refreshCredential(ref); }), 'ui-settings-web-search: credential invalidations');
    ctx.effect(() => ctx.configForms.whileServed([WEB_SEARCH_NS], () => ctx.slots.inject('plugins.item', () => ctx.slots.register({
        name: 'plugins.item', id: 'web-search', order: 20, label: () => t('title'), locale: NS, inject: () => card.inject(),
    }, WebSearchCard))), 'ui-settings-web-search: page');
}
//# sourceMappingURL=index.js.map