/** Discover state: configured index sources, the selected index's query and loaded results, and source editing. */
import { NPMMIRROR_REGISTRY } from '@deepseek-ai/dsh-plugin-manager/registry';
import { createSnapshotStore } from '@deepseek-ai/dsh-client-store';
import { declaredQuery, INITIAL_QUERY } from "./presentation.js";
/** Plugins loaded before "Show more", and loaded again by each press. */
export const PAGE_SIZE = 50;
/**
 * The identity of a detail read.
 * @param request - the plugin.
 * @param language - the display language.
 * @returns the key of the read in {@link DetailsState}.
 */
export function detailKey(request, language) {
    return JSON.stringify([language, request]);
}
/** Largest page the Host returns, the `limit` bound of `pluginMarket.search`. */
const MAX_PAGE = 100;
const INITIAL = {
    status: 'idle', indexes: [], selected: null, listing: null, editing: false, draft: '', saving: false, sourceFailure: null,
};
function sourceFailure(failure) {
    if (failure.code === 'plugin-market/invalid-index-url')
        return { kind: failure.details.reason === 'duplicate' ? 'duplicate' : 'invalid' };
    if (failure.code === 'plugin-market/settings-unavailable')
        return { kind: 'unavailable' };
    return { kind: 'other', reason: failure.message };
}
function detailFailure(failure) {
    return failure.code === 'plugin-market/detail-unavailable' ? failure.details.reason : 'other';
}
/** The Host route of images beside detail documents, relative to the application base. */
const DETAIL_FILE_ROUTE = 'api/plugin-market/detail-file';
function listingFailure(failure) {
    return failure.code === 'plugin-market/invalid-index' ? 'invalid' : 'fetch';
}
/** Owns Discover reads and source edits for one Client. */
export class DiscoverController {
    ctx;
    base;
    /** Shared Discover state published to the tab. */
    store = createSnapshotStore(INITIAL);
    /** Detail reads published to detail sections. */
    details = createSnapshotStore({});
    disposed = false;
    /** The latest detail read of each key; an older read's answer is dropped. */
    detailReads = new Map();
    /** Numbers detail reads across the controller's lifetime, so a read started before a reset never matches one after it. */
    detailSequence = 0;
    indexRead = 0;
    listingRead = 0;
    /** Select the last source once the list it was added to arrives. */
    selectAdded = false;
    /** Runs the listing read that failed last. */
    failed = () => { };
    /**
     * @param ctx - the plugin context whose `remote.pluginMarket` namespace answers.
     * @param base - the application base URL that Host routes resolve against.
     */
    constructor(ctx, base) {
        this.ctx = ctx;
        this.base = base;
    }
    /** Stop publishing late settlements. */
    dispose() {
        this.disposed = true;
    }
    /**
     * Build the tab's injected face.
     * @param resolveText - current-language text resolution from the locale service.
     * @returns the tab's injected state sources and actions.
     */
    face(resolveText) {
        const detail = this.detailFace();
        return {
            ...detail,
            hooks: { ...detail.hooks, discover: this.store },
            resolveText,
            ensure: () => { if (this.state().status === 'idle')
                void this.loadIndexes(); },
            reload: () => { void this.loadIndexes(); },
            select: (url) => {
                if (this.state().selected === url)
                    return;
                this.patch({ selected: url });
                void this.load(url, INITIAL_QUERY, PAGE_SIZE, false);
            },
            search: (query) => {
                const listing = this.state().listing;
                if (listing !== null)
                    void this.load(listing.url, query, PAGE_SIZE, false);
            },
            more: () => { void this.loadMore(); },
            retry: () => { this.failed(); },
            refresh: () => {
                const listing = this.state().listing;
                if (listing !== null)
                    void this.load(listing.url, listing.query, PAGE_SIZE, true);
            },
            toggleSources: () => { this.patch({ editing: !this.state().editing, sourceFailure: null }); },
            editDraft: (text) => { this.patch({ draft: text, sourceFailure: null }); },
            addSource: () => {
                const draft = this.state().draft.trim();
                if (draft === '')
                    return;
                this.selectAdded = true;
                void this.saveSources([...this.state().indexes.map(index => index.url), draft], true);
            },
            removeSource: (url) => { void this.saveSources(this.state().indexes.map(index => index.url).filter(item => item !== url), false); },
        };
    }
    /** Detail reads and image addressing of the entry detail section. */
    detailFace() {
        return {
            hooks: { details: this.details, locale: this.ctx.locale },
            loadDetail: (request, language) => {
                if (this.details.getSnapshot()[detailKey(request, language)] === undefined)
                    void this.readDetail(request, language);
            },
            retryDetail: (request, language) => { void this.readDetail(request, language, true); },
            detailImage: (document, destination) => {
                if (/^(?:[a-z][a-z\d+.-]*:|\/\/|#)/iu.test(destination))
                    return undefined;
                return new URL(`${DETAIL_FILE_ROUTE}?document=${encodeURIComponent(document)}&path=${encodeURIComponent(destination)}`, this.base()).href;
            },
        };
    }
    /**
     * Forget every detail read after the connection reset: a restarted Host no longer knows the document ids that address
     * their images, and each shown section reads its document again.
     */
    detailsReset() {
        this.detailReads.clear();
        this.details.set({});
    }
    /** Re-read the sources after the Host announced a change or the connection reset; an unrendered tab stays idle. */
    sourcesChanged() {
        if (this.state().status !== 'idle')
            void this.loadIndexes();
    }
    /** Recompute installation status of every loaded plugin after the profile's bundles changed; the Host reuses its cached document. */
    bundlesChanged() {
        const { listing } = this.state();
        // A ready listing holds the results it settled with.
        if (listing?.status === 'ready')
            void this.load(listing.url, listing.query, Math.max(listing.value.entries.length, PAGE_SIZE), false);
    }
    async readDetail(request, language, refresh = false) {
        const key = detailKey(request, language);
        const read = ++this.detailSequence;
        this.detailReads.set(key, read);
        this.publishDetail(key, { status: 'loading' });
        // The remote is captured before any wait: the plugin's context may be disposed while the comparison runs.
        const { remote } = this.ctx;
        const current = () => !this.disposed && this.detailReads.get(key) === read;
        // Only an npm version waits for the registry comparison; a Git version is read at once.
        // npmmirror is asked first where its registry answered the Host's comparison fastest.
        let npmFileSources = [];
        if (request.npm) {
            const fastest = await remote.pluginRegistryProbe.fastest();
            // A retry or reset started while the comparison ran settles the key instead.
            if (!current())
                return;
            if (fastest.ok && fastest.value === NPMMIRROR_REGISTRY)
                npmFileSources = ['npmmirror'];
        }
        const { url, name, version } = request;
        const answer = await remote.pluginMarket.readEntryDetail(url, name, version, language, { refresh, npmFileSources });
        if (current())
            this.publishDetail(key, answer.ok ? { status: 'ready', detail: answer.value } : { status: 'error', failure: detailFailure(answer.error) });
    }
    publishDetail(key, state) {
        this.details.set({ ...this.details.getSnapshot(), [key]: state });
    }
    state() {
        return this.store.getSnapshot();
    }
    patch(next) {
        this.store.set({ ...this.state(), ...next });
    }
    async loadIndexes() {
        const read = ++this.indexRead;
        if (this.state().status === 'idle')
            this.patch({ status: 'loading' });
        const answer = await this.ctx.remote.pluginMarket.listIndexes();
        if (this.disposed || read !== this.indexRead)
            return;
        if (!answer.ok) {
            this.patch({ status: 'error' });
            return;
        }
        const indexes = answer.value;
        const previous = this.state().selected;
        const last = indexes.at(-1);
        const selected = this.selectAdded && last !== undefined
            ? last.url
            : indexes.some(index => index.url === previous) ? previous : indexes[0]?.url ?? null;
        this.selectAdded = false;
        this.patch({ status: 'ready', indexes, selected, ...indexes.length === 0 ? { editing: false } : {} });
        if (selected === null)
            this.patch({ listing: null });
        else if (selected !== this.state().listing?.url)
            void this.load(selected, INITIAL_QUERY, PAGE_SIZE, false);
    }
    /**
     * Load the first `count` results of a query in pages the Host accepts, replacing the shown results at once. Later
     * pages pin the first page's document; when the Host answers from a replaced document the read restarts once, and
     * fails if the document is replaced again. The index's undeclared ids leave the query.
     */
    async load(url, query, count, refresh) {
        const read = ++this.listingRead;
        const current = this.state().listing;
        const [kept, answered] = current?.url === url ? [current.value, current.answered] : [null, null];
        this.patch({ listing: { url, query, status: 'loading', value: kept, answered, failure: null } });
        const fail = (failure) => {
            this.failed = () => { void this.load(url, query, count, refresh); };
            return { url, query, status: 'error', value: kept, answered, failure };
        };
        let value = null;
        let restarted = false;
        while (value === null || (value.entries.length < count && value.nextOffset !== null)) {
            const offset = value?.entries.length ?? 0;
            const limit = Math.min(count - offset, MAX_PAGE);
            const answer = await this.page(url, query, offset, limit, value?.fetchedAt, refresh && value === null && !restarted);
            if (this.disposed || read !== this.listingRead)
                return;
            if (!answer.ok) {
                this.settle(url, fail(listingFailure(answer.error)), answer);
                return;
            }
            if (value === null || answer.value.fetchedAt === value.fetchedAt) {
                value = value === null ? answer.value : { ...answer.value, entries: [...value.entries, ...answer.value.entries] };
            }
            else if (restarted) {
                this.patch({ listing: fail('changed') });
                return;
            }
            else {
                restarted = true;
                value = null;
            }
        }
        const declared = declaredQuery(query, value.metadata);
        if (declared !== query) {
            void this.load(url, declared, count, false);
            return;
        }
        this.settle(url, { url, query, status: 'ready', value, answered: query, failure: null }, { ok: true, value });
    }
    /** Load the page after the shown results; a replaced document reloads them from the first page. */
    async loadMore() {
        const listing = this.state().listing;
        // A failed next page leaves status `error` with results that still answer the query.
        if (listing === null || (listing.status !== 'ready' && listing.status !== 'error') || listing.answered !== listing.query)
            return;
        if (listing.value?.nextOffset == null)
            return;
        const { url, query, value } = listing;
        const read = ++this.listingRead;
        this.patch({ listing: { ...listing, status: 'more', failure: null } });
        const answer = await this.page(url, query, value.entries.length, PAGE_SIZE, value.fetchedAt, false);
        if (this.disposed || read !== this.listingRead)
            return;
        if (!answer.ok) {
            this.failed = () => { void this.loadMore(); };
            this.settle(url, { ...listing, status: 'error', failure: listingFailure(answer.error) }, answer);
            return;
        }
        if (answer.value.fetchedAt !== value.fetchedAt) {
            void this.load(url, query, value.entries.length + PAGE_SIZE, false);
            return;
        }
        const next = { ...answer.value, entries: [...value.entries, ...answer.value.entries] };
        this.settle(url, { ...listing, status: 'ready', value: next, failure: null }, { ok: true, value: next });
    }
    page(url, query, offset, limit, fetchedAt, refresh) {
        return this.ctx.remote.pluginMarket.search(url, {
            ...query.text.trim() === '' ? {} : { text: query.text },
            ...query.category === null ? {} : { category: query.category },
            ...query.labels.length === 0 ? {} : { labels: query.labels },
            sort: query.sort, offset, limit, ...fetchedAt === undefined ? {} : { fetchedAt }, ...refresh ? { refresh: true } : {},
        });
    }
    /** Publish a settled read and the index title or error it taught about its source. */
    settle(url, listing, answer) {
        const indexes = this.state().indexes.map((index) => {
            if (index.url !== url)
                return index;
            if (!answer.ok)
                return { ...index, error: answer.error.message };
            const { title, description } = answer.value;
            return { url, title, ...description === undefined ? {} : { description } };
        });
        this.patch({ listing, indexes });
    }
    async saveSources(urls, adding) {
        this.patch({ saving: true, sourceFailure: null });
        const answer = await this.ctx.remote.pluginMarket.setIndexes(urls);
        if (this.disposed)
            return;
        if (!answer.ok) {
            this.selectAdded = false;
            this.patch({ saving: false, sourceFailure: sourceFailure(answer.error) });
            return;
        }
        this.patch({ saving: false, ...adding ? { draft: '' } : {} });
        await this.loadIndexes();
    }
}
//# sourceMappingURL=market-store.js.map