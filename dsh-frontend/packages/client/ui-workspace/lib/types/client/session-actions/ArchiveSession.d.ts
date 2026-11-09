import type { ArchiveSessionInjected, SessionArchiveConfirmProps, SessionMenuItemProps, SessionRowActionProps } from '../contract/slots.ts';
/**
 * Menu row (order 400): archive, or restore an archived row. Absent while the Session's access
 * snapshot excludes that toggle.
 * @param props - owner share, the archive share, the access Hook, and the menu open state.
 * @returns the row, or nothing when the caller may not toggle this Session's archival.
 */
export declare function ArchiveSessionMenuItem({ sessionId, useArchived, useMenuOpenState, useShortcuts, useSessionAccess, archiveSession, unarchiveSession, t, }: SessionMenuItemProps<ArchiveSessionInjected>): import("react").JSX.Element | null;
/**
 * Hover button (order 100): archive, or restore an archived row. Absent while the Session's access
 * snapshot excludes that toggle.
 * @param props - owner share, the access Hook, and the archive share.
 * @returns the button, or nothing when the caller may not toggle this Session's archival.
 */
export declare function ArchiveSessionRowButton({ sessionId, useArchived, useSessionAccess, archiveSession, unarchiveSession, t, }: SessionRowActionProps<ArchiveSessionInjected>): import("react").JSX.Element | null;
/**
 * The `shell.overlay` entry: nothing while no confirmation is pending,
 * otherwise one dialog per request (keyed by the Session). Confirming asks
 * the Host to stop the listed work and archive; cancelling leaves the
 * Session running and visible.
 * @param props - the request hook, its settlement, the stop-and-archive hop, and the locale seat.
 * @returns the open dialog, or null.
 */
export declare function SessionArchiveConfirmDialog({ useArchiveRequest, settleSessionArchive, stopAndArchiveSession, t, }: SessionArchiveConfirmProps): import("react").JSX.Element | null;
//# sourceMappingURL=ArchiveSession.d.ts.map