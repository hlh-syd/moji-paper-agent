import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { IconChevronDownOutlineRegular, IconChevronRightOutlineRegular, IconDragHandleOutlineRegular, IconTrashOutlineRegular, Tooltip, useRowDragPreview, } from '@deepseek-ai/dsh-client-ui-primitives';
import { ModelInputTypes } from "./ModelInputTypes.js";
import { ModelReasoningEfforts } from "./ModelReasoningEfforts.js";
import styles from './ModelsSection.module.css';
function rowHalf(event) {
    const rect = event.currentTarget.getBoundingClientRect();
    return event.clientY < rect.top + rect.height / 2 ? 'before' : 'after';
}
/**
 * Render consistent model identity, capacity, and input-type controls.
 * @param props - drafted fields and their owning editor's actions.
 * @returns one expandable model entry.
 */
export function ModelRow(props) {
    const { model, position, t, disabled } = props;
    const preview = useRowDragPreview(styles['modelDragPreview']);
    return (_jsxs("div", { className: styles['modelEntry'], "data-row-key": props.rowKey, "data-dragging": props.order.dragging || undefined, "data-drop-target": props.order.dragging ? undefined : props.order.target, onDragOver: (event) => {
            if (!props.order.canDrop)
                return;
            event.preventDefault();
            event.dataTransfer.dropEffect = 'move';
            props.order.onOver(rowHalf(event));
        }, onDragLeave: (event) => {
            if (event.relatedTarget instanceof Node && event.currentTarget.contains(event.relatedTarget))
                return;
            props.order.onLeave();
        }, onDrop: (event) => {
            if (!props.order.canDrop)
                return;
            event.preventDefault();
            props.order.onDrop(rowHalf(event));
        }, children: [_jsxs("div", { className: styles['modelRow'], children: [_jsx(Tooltip, { label: t('reorderModelHelp'), children: _jsx("button", { type: "button", className: `${styles['iconButton']} ${styles['modelDragHandle']}`, "aria-label": `${t('reorderModel')} ${String(position)}`, "aria-keyshortcuts": "ArrowUp ArrowDown", "aria-description": t('reorderModelHelp'), disabled: disabled, draggable: !disabled, onDragStart: (event) => {
                                event.dataTransfer.effectAllowed = 'move';
                                event.dataTransfer.setData('application/x-dsh-model-order', String(position));
                                props.order.onStart();
                                preview.start(event, event.currentTarget.closest('[data-row-key]'));
                            }, onDragEnd: () => { if (preview.clear())
                                props.order.onEnd(); }, onKeyDown: (event) => {
                                if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown')
                                    return;
                                event.preventDefault();
                                event.stopPropagation();
                                props.order.onMove(event.key === 'ArrowUp' ? -1 : 1);
                            }, children: _jsx(IconDragHandleOutlineRegular, { size: 14 }) }) }), ['id', 'name'].map(field => (_jsx("input", { className: styles['input'], type: "text", value: typeof model[field] === 'string' ? model[field] : '', placeholder: t(field === 'id' ? 'modelId' : 'modelName'), "aria-label": `${t(field === 'id' ? 'modelId' : 'modelName')} ${String(position)}`, disabled: disabled, onChange: (event) => {
                            const value = event.target.value;
                            props.onFieldChange(field, field === 'name' && value === '' ? undefined : value);
                        }, onBlur: field === 'id' ? event => props.onIdBlur?.(event.target.value) : undefined }, field))), _jsx("button", { type: "button", className: styles['iconButton'], "aria-label": `${t('modelAdvanced')} ${String(position)}`, "aria-expanded": props.expanded, title: t('modelAdvanced'), onClick: props.onToggle, children: props.expanded ? _jsx(IconChevronDownOutlineRegular, {}) : _jsx(IconChevronRightOutlineRegular, {}) }), _jsx("button", { type: "button", className: `${styles['iconButton']} ${styles['iconButtonDanger']}`, "aria-label": `${t('removeModel')} ${String(position)}`, title: t('removeModel'), disabled: disabled, onClick: props.onRemove, children: _jsx(IconTrashOutlineRegular, { size: 14 }) })] }), props.expanded
                ? (_jsxs("div", { className: styles['modelAdvanced'], children: [['contextWindow', 'maxTokens'].map(field => (_jsxs("label", { className: styles['modelField'], children: [_jsx("span", { className: styles['modelFieldLabel'], children: t(field) }), _jsx("input", { className: styles['input'], type: "text", inputMode: "numeric", value: props[field].value, placeholder: props[field].placeholder, "aria-label": `${t(field)} ${String(position)}`, disabled: disabled, onChange: (event) => { props[field].onChange(event.target.value); }, onBlur: props[field].onBlur })] }, field))), _jsx(ModelInputTypes, { model: model, field: props.inputField, position: position, fallback: props.inputFallback, disabled: disabled || props.inputLoading === true, t: t, onChange: props.onChange }), props.reasoningLevels !== undefined && props.reasoningLevels.length > 0
                            ? (_jsx(ModelReasoningEfforts, { model: model, levels: props.reasoningLevels, position: position, disabled: disabled, t: t, onChange: props.onChange }))
                            : null] }))
                : null] }));
}
//# sourceMappingURL=ModelRow.js.map