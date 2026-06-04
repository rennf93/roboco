/**
 * Unit tests for Prompter store dirty-bit protection.
 *
 * Acceptance criterion: "A unit test confirms that a user-edited draft field
 * is NOT overwritten when the LLM subsequently updates that field."
 */

import { describe, it, expect, beforeEach } from "vitest";
import { usePrompterStore } from "@/store/prompter-store";
import { Team } from "@/types";

// Reset store between tests
function resetStore() {
  usePrompterStore.setState({
    activeConversationId: null,
    conversations: {},
    streamingStatus: "idle",
    selectedModel: "claude-sonnet-4-5",
    showLaunchSummary: false,
  });
}

describe("Prompter store — dirty-bit field protection", () => {
  beforeEach(() => {
    resetStore();
  });

  it("LLM draft_update overwrites a clean (un-edited) field", () => {
    const store = usePrompterStore.getState();
    const id = store.startConversation();

    // Field has not been user-edited → dirty = false
    const before = usePrompterStore.getState().conversations[id].draft.title;
    expect(before.dirty).toBe(false);

    // LLM sends a draft_update for title
    usePrompterStore.getState().setFieldFromLLM("title", "LLM-generated title");

    const after = usePrompterStore.getState().conversations[id].draft.title;
    expect(after.value).toBe("LLM-generated title");
    expect(after.dirty).toBe(false);
  });

  it("LLM draft_update does NOT overwrite a user-edited (dirty) field", () => {
    const store = usePrompterStore.getState();
    const id = store.startConversation();

    // User edits the title field
    usePrompterStore.getState().setFieldFromUser("title", "My custom title");

    const afterUserEdit =
      usePrompterStore.getState().conversations[id].draft.title;
    expect(afterUserEdit.value).toBe("My custom title");
    expect(afterUserEdit.dirty).toBe(true);

    // LLM tries to overwrite
    usePrompterStore.getState().setFieldFromLLM("title", "LLM-generated title");

    const afterLLM =
      usePrompterStore.getState().conversations[id].draft.title;
    // User's value must be preserved
    expect(afterLLM.value).toBe("My custom title");
    expect(afterLLM.dirty).toBe(true);
  });

  it("dirty flag is isolated per field — clean fields still receive LLM updates", () => {
    const store = usePrompterStore.getState();
    const id = store.startConversation();

    // User edits only title
    usePrompterStore.getState().setFieldFromUser("title", "My title");

    // LLM updates description (clean)
    usePrompterStore
      .getState()
      .setFieldFromLLM("description", "LLM description");

    const draft = usePrompterStore.getState().conversations[id].draft;
    expect(draft.title.value).toBe("My title"); // user's value unchanged
    expect(draft.title.dirty).toBe(true);
    expect(draft.description.value).toBe("LLM description"); // LLM value applied
    expect(draft.description.dirty).toBe(false);
  });

  it("clearFieldDirty resets the dirty bit, allowing subsequent LLM updates", () => {
    const store = usePrompterStore.getState();
    const id = store.startConversation();

    // User edits the field
    usePrompterStore.getState().setFieldFromUser("title", "User title");
    expect(
      usePrompterStore.getState().conversations[id].draft.title.dirty
    ).toBe(true);

    // User clears their override
    usePrompterStore.getState().clearFieldDirty("title");
    expect(
      usePrompterStore.getState().conversations[id].draft.title.dirty
    ).toBe(false);

    // Now LLM can overwrite
    usePrompterStore.getState().setFieldFromLLM("title", "New LLM title");
    expect(
      usePrompterStore.getState().conversations[id].draft.title.value
    ).toBe("New LLM title");
  });

  it("setFieldFromUser marks the field dirty and stores the new value", () => {
    const store = usePrompterStore.getState();
    const id = store.startConversation();

    usePrompterStore
      .getState()
      .setFieldFromUser("team", Team.BACKEND);

    const draft = usePrompterStore.getState().conversations[id].draft;
    expect(draft.team.value).toBe(Team.BACKEND);
    expect(draft.team.dirty).toBe(true);
  });

  it("draft_update on acceptance_criteria does not overwrite when dirty", () => {
    const store = usePrompterStore.getState();
    const id = store.startConversation();

    const userCriteria = ["Must load in under 1s", "Must be accessible"];
    usePrompterStore
      .getState()
      .setFieldFromUser("acceptance_criteria", userCriteria);

    // LLM sends different criteria
    usePrompterStore.getState().setFieldFromLLM("acceptance_criteria", [
      "LLM criterion 1",
      "LLM criterion 2",
    ]);

    const draft = usePrompterStore.getState().conversations[id].draft;
    expect(draft.acceptance_criteria.value).toEqual(userCriteria);
    expect(draft.acceptance_criteria.dirty).toBe(true);
  });
});
