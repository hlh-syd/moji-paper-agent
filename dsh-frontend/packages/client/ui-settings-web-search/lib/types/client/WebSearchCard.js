import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { SettingsForm, SettingsSecretField, SettingsValueField, Switch } from '@deepseek-ai/dsh-client-ui-primitives';
import { formLabels } from "./locales.js";
import styles from './WebSearchCard.module.css';
/**
 * Render the web-search provider's one-liner or its settings form, as the Plugins page asks.
 * @param props - the view asked for, locale copy, the form snapshot, and its actions.
 * @returns the one-liner, or the form.
 */
export function WebSearchCard(props) {
    const { t } = props;
    const state = props.useWebSearchCard(snapshot => snapshot);
    if (props.view === 'summary')
        return t('description');
    const disabled = !state.writable || state.saving;
    const searchApi = state.useSearchApi;
    const limitField = searchApi ? 'count' : 'maxUses';
    return (_jsxs(SettingsForm, { labels: formLabels(t), state: state, onSave: props.save, onDiscard: props.discard, children: [state.apiSwitchAvailable && (_jsxs("div", { className: styles.feature, children: [_jsxs("label", { className: styles.toggleRow, children: [_jsx("span", { children: t('useSearchApi') }), _jsx(Switch, { checked: searchApi, label: t('useSearchApi'), disabled: disabled || state.apiSwitchBlocked, onChange: (checked) => { props.setSearchApi(checked); } })] }), _jsxs("p", { className: styles.hint, children: [t('useSearchApiHint'), _jsx("a", { className: styles.link, href: t('searchGuideUrl'), target: "_blank", rel: "noopener noreferrer", children: t('searchGuide') })] })] })), _jsx(SettingsSecretField, { id: "plugin-config-web-search-key", label: t('apiKey'), hint: t('apiKeyHint'), 
                // The credentials domain accepts a key even when the settings document
                // itself is read-only; they are separate stores with separate refusals.
                // Its own writability is what disables this control — a key sourced
                // from the process environment cannot be written from here.
                disabled: !state.apiKeyWritable, text: state.apiKey.text, configured: state.apiKeyConfigured, stateLabel: state.apiKeyConfigured ? t('apiKeySet') : t('apiKeyUnset'), onEdit: (text) => { props.edit('apiKey', text); } }), _jsx(SettingsValueField, { id: "plugin-config-web-search-endpoint", label: t(searchApi ? 'searchApiBaseUrl' : 'baseUrl'), hint: t(searchApi ? 'searchApiBaseUrlHint' : 'baseUrlHint'), overriddenLabel: t('overridden'), resetLabel: t('reset'), invalidLabel: t('invalidNumber'), disabled: disabled, ...state.baseURL, onEdit: (text) => { props.edit('baseURL', text); }, onReset: () => { props.resetField('baseURL'); } }), _jsx(SettingsValueField, { id: searchApi ? 'plugin-config-web-search-count' : 'plugin-config-web-search-max-uses', label: t(limitField), hint: t(searchApi ? 'countHint' : 'maxUsesHint'), overriddenLabel: t('overridden'), resetLabel: t('reset'), invalidLabel: t(searchApi ? 'invalidCount' : 'invalidNumber'), numeric: true, disabled: disabled, ...state[limitField], onEdit: (text) => { props.edit(limitField, text); }, onReset: () => { props.resetField(limitField); } })] }));
}
//# sourceMappingURL=WebSearchCard.js.map