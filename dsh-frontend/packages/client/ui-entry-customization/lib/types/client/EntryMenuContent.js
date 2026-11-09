import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
/** Folded Conversation Views, plugin discovery while reachable, and creation. */
import { IconCheckOutlineRegular, IconPluginPinwheelOutlineRegular, IconPlusOutlineRegular, MenuItemButton, } from '@deepseek-ai/dsh-client-ui-primitives';
import css from './EntryMenu.module.css';
/**
 * Render folded Conversation Views followed by discovery and Creator actions, without pin controls.
 * @param props - Owner dismissal callback, injected actions, and localized copy.
 * @returns Menu rows for the region's More menu.
 */
export function EntryMenuContent({ entries, onOpen, region, onDismiss, usePluginNavigation, browsePlugins, createPlugin, t, }) {
    const moreEntries = region === 'conversation' ? entries : [];
    const browsable = usePluginNavigation(present => present);
    return _jsxs(_Fragment, { children: [moreEntries.map(entry => _jsx(MenuItemButton, { current: entry.active === true, onSelect: () => { onDismiss(); onOpen(entry.id); }, children: _jsxs("span", { className: css.entryName, children: [_jsx("span", { className: css.entryTitle, children: entry.label }), entry.active === true && _jsx(IconCheckOutlineRegular, { size: 16, className: css.activeMark })] }) }, entry.id)), browsable && _jsx(MenuItemButton, { separatorBefore: moreEntries.length > 0, icon: _jsx(IconPluginPinwheelOutlineRegular, { size: 16 }), onSelect: () => { onDismiss(); browsePlugins(); }, children: t('install') }), _jsx(MenuItemButton, { icon: _jsx(IconPlusOutlineRegular, { size: 16 }), separatorBefore: !browsable && moreEntries.length > 0, onSelect: () => { onDismiss(); createPlugin(); }, children: t('create') })] });
}
//# sourceMappingURL=EntryMenuContent.js.map