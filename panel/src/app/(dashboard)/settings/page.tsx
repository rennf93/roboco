"use client";

import { useTheme } from "next-themes";
import { useUIStore } from "@/store";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { HelpTip } from "@/components/ui/help-tip";
import { Settings, Palette, Bell, Server, User } from "lucide-react";
import { API_URL, WS_URL } from "@/lib/constants";
import { TranscriptRetentionCard } from "@/components/settings/transcript-retention-card";
import { FeatureFlagsCard } from "@/components/settings/feature-flags-card";

export default function SettingsPage() {
  const { theme, setTheme } = useTheme();
  const {
    sidebarCollapsed,
    setSidebarCollapsed,
    notificationsEnabled,
    setNotificationsEnabled,
    soundEnabled,
    setSoundEnabled,
    autoRefresh,
    setAutoRefresh,
    refreshIntervalSeconds,
    setRefreshIntervalSeconds,
  } = useUIStore();

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Settings</h1>
        <p className="text-muted-foreground">
          Configure your RoboCo Control Panel preferences
        </p>
      </div>

      {/* Cards grid — two columns on large screens. Order (row,col):
          User Info (1,1) · Appearance (1,2) · Data & Refresh (2,1) ·
          Transcript Retention (2,2) · Notifications (3,1) · Connection Info (3,2). */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* User Info */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <User className="h-5 w-5" />
              User Info
            </CardTitle>
            <CardDescription>Your account information</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center gap-4">
              <div className="h-16 w-16 rounded-full bg-primary flex items-center justify-center">
                <span className="text-primary-foreground font-bold text-2xl">
                  CEO
                </span>
              </div>
              <div>
                <p className="font-semibold text-lg">Renzo</p>
                <p className="text-sm text-muted-foreground">
                  Chief Executive Officer
                </p>
                <HelpTip label="The CEO's fixed agent id — used to attribute your notifications, notes, and approvals across the API.">
                  <p className="text-xs text-muted-foreground mt-1 w-fit">
                    Agent ID: 00000000-0000-0000-0000-000000000001
                  </p>
                </HelpTip>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Appearance */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Palette className="h-5 w-5" />
              Appearance
            </CardTitle>
            <CardDescription>
              Customize the look and feel of the panel
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <HelpTip label="Saved to this browser only — doesn't sync across devices.">
                  <Label>Theme</Label>
                </HelpTip>
                <p className="text-sm text-muted-foreground">
                  Select your preferred color scheme
                </p>
              </div>
              <Select value={theme} onValueChange={setTheme}>
                <SelectTrigger className="w-auto min-w-28">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="light">Light</SelectItem>
                  <SelectItem value="dark">Dark</SelectItem>
                  <SelectItem value="system">System</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <Separator />
            <div className="flex items-center justify-between">
              <div>
                <HelpTip label="Saved to this browser only — doesn't sync across devices.">
                  <Label>Collapsed Sidebar</Label>
                </HelpTip>
                <p className="text-sm text-muted-foreground">
                  Show icons only in the sidebar
                </p>
              </div>
              <Switch
                checked={sidebarCollapsed}
                onCheckedChange={setSidebarCollapsed}
              />
            </div>
          </CardContent>
        </Card>

        {/* Data & Refresh — client-only prefs, instant-apply (same idiom as
            Theme/Sidebar above); never sent to the backend. */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Server className="h-5 w-5" />
              Data & Refresh
            </CardTitle>
            <CardDescription>Configure data fetching behavior</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <HelpTip label="Also enables the Refresh Interval picker below.">
                  <Label>Auto Refresh</Label>
                </HelpTip>
                <p className="text-sm text-muted-foreground">
                  Periodically re-fetch the current page&apos;s data
                </p>
              </div>
              <Switch checked={autoRefresh} onCheckedChange={setAutoRefresh} />
            </div>
            <Separator />
            <div className="flex items-center justify-between">
              <div>
                <HelpTip
                  label={
                    !autoRefresh
                      ? "Disabled — turn on Auto Refresh above to pick an interval."
                      : undefined
                  }
                >
                  <Label>Refresh Interval</Label>
                </HelpTip>
                <p className="text-sm text-muted-foreground">
                  How often to fetch new data (seconds)
                </p>
              </div>
              <Select
                value={String(refreshIntervalSeconds)}
                onValueChange={(v) => setRefreshIntervalSeconds(Number(v))}
                disabled={!autoRefresh}
              >
                <SelectTrigger className="w-auto min-w-20">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="10">10s</SelectItem>
                  <SelectItem value="30">30s</SelectItem>
                  <SelectItem value="60">1m</SelectItem>
                  <SelectItem value="300">5m</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </CardContent>
        </Card>

        {/* Transcript Retention (panel-tunable; persisted server-side) */}
        <TranscriptRetentionCard />

        {/* Notifications — client-only prefs, instant-apply. */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Bell className="h-5 w-5" />
              Notifications
            </CardTitle>
            <CardDescription>Configure how you receive updates</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <HelpTip label="Also gates Sound Alerts below.">
                  <Label>Enable Notifications</Label>
                </HelpTip>
                <p className="text-sm text-muted-foreground">
                  Toast + bell for incoming agent notifications
                </p>
              </div>
              <Switch
                checked={notificationsEnabled}
                onCheckedChange={setNotificationsEnabled}
              />
            </div>
            <Separator />
            <div className="flex items-center justify-between">
              <div>
                <HelpTip
                  label={
                    !notificationsEnabled
                      ? "Disabled — turn on Enable Notifications above first."
                      : undefined
                  }
                >
                  <Label>Sound Alerts</Label>
                </HelpTip>
                <p className="text-sm text-muted-foreground">
                  Chime on new notifications
                </p>
              </div>
              <Switch
                checked={soundEnabled}
                onCheckedChange={setSoundEnabled}
                disabled={!notificationsEnabled}
              />
            </div>
          </CardContent>
        </Card>

        {/* Connection Info */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Settings className="h-5 w-5" />
              Connection Info
            </CardTitle>
            <CardDescription>
              Backend API configuration (read-only)
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <HelpTip label="Read-only — set via NEXT_PUBLIC_API_URL at build time.">
                <Label>API URL</Label>
              </HelpTip>
              <Input value={API_URL} readOnly className="bg-muted" />
            </div>
            <div className="space-y-2">
              <HelpTip label="Read-only — set via NEXT_PUBLIC_WS_URL at build time.">
                <Label>WebSocket URL</Label>
              </HelpTip>
              <Input value={WS_URL} readOnly className="bg-muted" />
            </div>
            <p className="text-xs text-muted-foreground">
              These values are configured via environment variables
              (NEXT_PUBLIC_API_URL, NEXT_PUBLIC_WS_URL)
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Feature Flags — master switches for optional subsystems (full width;
          persisted server-side, applied on next restart). The X (Twitter)
          credentials form nests as a collapsible under the X-engine flag. */}
      <FeatureFlagsCard />
    </div>
  );
}
