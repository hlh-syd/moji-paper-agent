import { DeepSeekModelLabel } from "./DeepSeekModelLabel.js";
import { en, ja, es, fr, de, ru, pt, zh } from "./locales.js";
/** Required services: model-label registration and localized badge text. */
export const inject = ['slots', 'locale'];
/**
 * Decorate existing model labels while their declaring slot is mounted.
 * @param ctx - Client plugin context owning the dictionaries and registration.
 */
export function apply(ctx) {
    const namespace = 'deepseek-model-label-1017';
    ctx.effect(() => ctx.locale.register(namespace, { zh, en, ja, es, fr, de, ru, pt }), 'deepseek-model-label-1017: dictionaries');
    ctx.slots.inject('conversation.input.model.label', () => ctx.slots.register({
        name: 'conversation.input.model.label', locale: namespace,
    }, DeepSeekModelLabel));
}
//# sourceMappingURL=index.js.map