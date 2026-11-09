import { jsx as _jsx } from "react/jsx-runtime";
/** A bundle's long description on its page: the Markdown document the Host reads for it. */
import { useEffect, useMemo } from 'react';
import { DetailDocument, detailDocumentState, useDetailDocumentLabels } from '@deepseek-ai/dsh-client-ui-primitives';
import { bundleDetailKey } from "./detail-store.js";
const FAILURE_KEYS = {
    'too-large': 'detailTooLarge',
    'network': 'detailNetwork',
    'denied': 'detailFailed',
    'git-unavailable': 'detailFailed',
    'invalid': 'detailInvalid',
    'other': 'detailFailed',
};
/**
 * Read and render a bundle's detail document in the active language; relative images load from the Host beside it.
 * @param props - the bundle, the language, the reads, and the page's callbacks.
 * @returns the section, or null when the bundle has no document.
 */
export function BundleDetailSection(props) {
    const { name, version, fromNpm, language, details, t, load, retry, image } = props;
    const key = bundleDetailKey(name, version, language);
    const state = details[key];
    useEffect(() => { load(name, version, language, fromNpm); }, [key, details]);
    const labels = useDetailDocumentLabels({
        title: t('detailTitle'), loading: t('detailLoading'), retry: t('retry'), copy: t('copy'), copied: t('copied'),
        codeBlock: t('codeBlock.title'), wrap: t('codeBlock.wrap'), unwrap: t('codeBlock.unwrap'), footnotes: t('detailFootnotes'),
    });
    const detail = state?.status === 'ready' ? state.detail : undefined;
    const document = detail?.status === 'found' ? detail.path : undefined;
    const images = useMemo(() => document === undefined ? undefined : { resolve: value => image(name, document, value, fromNpm) }, [name, document, fromNpm, image]);
    const shown = detailDocumentState(state?.status === 'error'
        ? { status: 'error', message: t(FAILURE_KEYS[state.failure]) }
        : state === undefined || state.status === 'loading' ? state : { status: 'ready', markdown: detail?.status === 'found' ? detail.markdown : undefined });
    return _jsx(DetailDocument, { state: shown, labels: labels, images: images, onRetry: () => { retry(name, version, language, fromNpm); } });
}
//# sourceMappingURL=BundleDetailSection.js.map