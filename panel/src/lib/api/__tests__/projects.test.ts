import { describe, it, expect, vi } from "vitest";
import { Team } from "@/types";

// Mock mode is what runs `projectsApi.create`'s offline git-provider
// auto-detect; the network client must never be reached.
vi.mock("@/lib/mock-data", () => ({ isMockMode: () => true }));
vi.mock("../client", () => ({ default: {} }));

import { projectsApi } from "../projects";

async function providerFor(gitUrl: string) {
  const project = await projectsApi.create({
    name: "p",
    slug: "p",
    git_url: gitUrl,
    assigned_cell: Team.BACKEND,
  });
  return project.git_provider;
}

describe("projectsApi.create (mock mode) git provider detection", () => {
  it.each([
    "https://github.com/rennf93/roboco.git",
    "HTTPS://GitHub.com/rennf93/roboco",
    "git@github.com:rennf93/roboco.git",
  ])("detects %s as github", async (gitUrl) => {
    expect(await providerFor(gitUrl)).toBe("github");
  });

  it.each([
    // The host merely starts with github.com (CodeQL
    // js/incomplete-url-substring-sanitization): a substring check said github.
    "https://github.com.evil.tld/x/y.git",
    // github.com only in the path.
    "https://gitea.example/github.com/x.git",
    "git@gitlab.com:github.com/x.git",
    "not a url",
  ])("does not detect %s as github", async (gitUrl) => {
    expect(await providerFor(gitUrl)).toBeNull();
  });

  it("keeps an explicitly chosen provider", async () => {
    const project = await projectsApi.create({
      name: "p",
      slug: "p",
      git_url: "https://github.com/rennf93/roboco.git",
      git_provider: "gitea",
      assigned_cell: Team.BACKEND,
    });
    expect(project.git_provider).toBe("gitea");
  });
});
