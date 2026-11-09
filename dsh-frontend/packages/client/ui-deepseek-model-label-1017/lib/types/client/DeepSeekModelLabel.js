import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Tag } from '@deepseek-ai/dsh-client-ui-primitives';
import css from './DeepSeekModelLabel.module.css';
const MODEL_NAME = 'DeepSeek-V4.1-Flash';
/**
 * Render the fixed 1017 release label for recognized official Flash entries.
 * @param props - Provider and model identity plus the badge dictionary.
 * @returns The base name with a badge, or the untouched catalog name.
 */
export function DeepSeekModelLabel({ provider, model, t }) {
    const official = provider.id === 'deepseek-account' || provider.id === 'deepseek-official';
    if (!official || model.id !== 'deepseek-flash' || model.name !== MODEL_NAME)
        return model.name;
    return _jsxs("span", { className: css.label, children: [_jsx("span", { children: model.name }), ' ', _jsx(Tag, { tone: "info", children: t('badge.label') })] });
}
//# sourceMappingURL=DeepSeekModelLabel.js.map