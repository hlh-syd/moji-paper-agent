import type { ChatViewSlotProps } from '../contract/slots.ts';
interface RunningStatusProps {
    readonly startTime: number | undefined;
    /** Freeze the shown duration while the Client cannot observe the Session. */
    readonly paused: boolean;
    readonly t: ChatViewSlotProps['t'];
}
/**
 * Show live elapsed time after the current Turn's content without announcing ticks.
 * @param props - Current Turn start time, pause state, and localized copy.
 * @returns the blue running indicator; mount only while the Session is running.
 */
export declare const RunningStatus: import("react").MemoExoticComponent<({ startTime, paused, t }: RunningStatusProps) => import("react").JSX.Element>;
export {};
//# sourceMappingURL=RunningStatus.d.ts.map