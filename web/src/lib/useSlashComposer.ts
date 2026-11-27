import { useState, type KeyboardEvent, type RefObject } from "react";
import { filterSlashCommands, type SlashCommandDef } from "./slashCommands";

interface UseSlashComposerOptions {
  value: string;
  setValue: (next: string) => void;
  inputRef: RefObject<HTMLTextAreaElement | null>;
  onSubmitText: (text: string) => void;
  onExecuteCommand: (cmd: string) => void;
}

export function useSlashComposer({
  value,
  setValue,
  inputRef,
  onSubmitText,
  onExecuteCommand,
}: UseSlashComposerOptions) {
  const [slashIndex, setSlashIndex] = useState(0);
  const [slashDismissed, setSlashDismissed] = useState(false);

  const slashItems = slashDismissed ? [] : filterSlashCommands(value);
  const activeSlashIndex = slashItems.length > 0 ? Math.min(slashIndex, slashItems.length - 1) : 0;

  const resetSlashMenu = () => {
    setSlashIndex(0);
    setSlashDismissed(false);
  };

  const selectSlashItem = (item: SlashCommandDef, fromTab = false) => {
    if ((fromTab && item.type !== "immediate") || item.type === "goal") {
      setValue(`${item.cmd} `);
      resetSlashMenu();
      inputRef.current?.focus();
      return;
    }
    resetSlashMenu();
    onExecuteCommand(item.cmd);
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (slashItems.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSlashIndex((activeSlashIndex + 1) % slashItems.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSlashIndex((activeSlashIndex - 1 + slashItems.length) % slashItems.length);
        return;
      }
      if (e.key === "Tab") {
        e.preventDefault();
        selectSlashItem(slashItems[activeSlashIndex], true);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setSlashDismissed(true);
        return;
      }
      if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
        e.preventDefault();
        selectSlashItem(slashItems[activeSlashIndex], false);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      onSubmitText(value);
    }
  };

  return {
    slashItems,
    activeSlashIndex,
    setSlashIndex,
    resetSlashMenu,
    selectSlashItem,
    handleKeyDown,
  };
}
