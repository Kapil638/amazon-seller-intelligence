"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Panel } from "@/components/ui/layout";
import {
  archiveScoringProfile,
  createScoringProfile,
  listScoringProfiles,
  ProductLookupError,
  updateScoringProfile,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import type { ScoringProfile, ScoringWeights } from "@/lib/types";
import { STANDARD_SCORING_PROFILE_ID, STANDARD_V2_WEIGHTS } from "@/lib/types";

const WEIGHT_FIELDS: Array<[keyof ScoringWeights, string]> = [
  ["title", "Title Optimization"],
  ["bullets", "Bullet Content & SEO Readiness"],
  ["description_a_plus", "Description & A+ Content"],
  ["media", "Media Coverage"],
  ["content_structure", "Content Structure & Readability"],
];

function weightsTotal(weights: ScoringWeights): number {
  return WEIGHT_FIELDS.reduce((sum, [key]) => sum + Number(weights[key] || 0), 0);
}

function cloneWeights(weights: ScoringWeights): ScoringWeights {
  return { ...weights };
}

const STANDARD_PROFILE: ScoringProfile = {
  id: STANDARD_SCORING_PROFILE_ID,
  name: "Standard V2",
  description: "Immutable Listing Intelligence V2 weights. This is the universal benchmark.",
  weights: STANDARD_V2_WEIGHTS,
  is_system: true,
  is_default: false,
  is_archived: false,
  editable: false,
  deletable: false,
  created_at: null,
  updated_at: null,
  archived_at: null,
};

export function ScoringProfileControls({
  selectedId,
  onSelect,
}: {
  selectedId: string;
  onSelect: (profileId: string) => void;
}) {
  const [profiles, setProfiles] = useState<ScoringProfile[]>([STANDARD_PROFILE]);
  const [error, setError] = useState<string | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [manageOpen, setManageOpen] = useState(false);
  const [editing, setEditing] = useState<ScoringProfile | null>(null);

  async function reload() {
    const next = await listScoringProfiles();
    setProfiles(next.items);
    return next.items;
  }

  useEffect(() => {
    void reload().catch((err: unknown) => {
      setError(err instanceof ProductLookupError ? err.message : "Scoring profiles could not be loaded.");
    });
  }, []);

  const selectable = profiles.filter((item) => !item.is_archived);

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Label htmlFor="scoring-profile" className="text-xs text-muted-foreground">
        Scoring Profile
      </Label>
      <select
        id="scoring-profile"
        className="h-9 rounded-md border border-border bg-surface px-2 text-sm"
        value={selectedId}
        onChange={(event) => {
          const value = event.target.value;
          if (value === "__create__") {
            setEditing(null);
            setEditorOpen(true);
            return;
          }
          if (value === "__manage__") {
            setManageOpen(true);
            return;
          }
          onSelect(value);
        }}
      >
        {selectable.map((profile) => (
          <option key={profile.id} value={profile.id}>
            {profile.name}
            {profile.is_system ? "" : profile.is_default ? " · org default" : ""}
          </option>
        ))}
        <option value="__create__">Create New Profile…</option>
        <option value="__manage__">Manage Profiles</option>
      </select>
      {error ? <p className="text-xs text-destructive">{error}</p> : null}

      {editorOpen ? (
        <ProfileEditorModal
          existing={editing}
          onClose={() => {
            setEditorOpen(false);
            setEditing(null);
          }}
          onSaved={async (profile) => {
            await reload();
            setEditorOpen(false);
            setEditing(null);
            onSelect(profile.id);
          }}
        />
      ) : null}

      {manageOpen ? (
        <ManageProfilesModal
          profiles={profiles.filter((item) => !item.is_system && !item.is_archived)}
          onClose={() => setManageOpen(false)}
          onChanged={async (nextSelected) => {
            const items = await reload();
            if (nextSelected && items.some((item) => item.id === nextSelected)) {
              onSelect(nextSelected);
            } else if (!items.some((item) => item.id === selectedId)) {
              onSelect(STANDARD_SCORING_PROFILE_ID);
            }
          }}
          onEdit={(profile) => {
            setManageOpen(false);
            setEditing(profile);
            setEditorOpen(true);
          }}
        />
      ) : null}
    </div>
  );
}

function ProfileEditorModal({
  existing,
  onClose,
  onSaved,
}: {
  existing: ScoringProfile | null;
  onClose: () => void;
  onSaved: (profile: ScoringProfile) => Promise<void>;
}) {
  const [name, setName] = useState(existing?.name || "");
  const [description, setDescription] = useState(existing?.description || "");
  const [weights, setWeights] = useState<ScoringWeights>(
    cloneWeights(existing?.weights || STANDARD_V2_WEIGHTS),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [setDefault, setSetDefault] = useState(existing?.is_default ?? false);
  const total = useMemo(() => Number(weightsTotal(weights).toFixed(2)), [weights]);
  const valid = total === 100 && name.trim().length > 0;

  return (
    <Modal title={existing ? "Edit scoring profile" : "Customize Scoring Profile"} onClose={onClose}>
      <p className="text-sm text-muted-foreground">
        Custom profiles change how section scores are weighted. They do not change the underlying
        analysis.
      </p>
      <div className="space-y-3">
        <div className="space-y-1.5">
          <Label htmlFor="profile-name">Profile name</Label>
          <Input
            id="profile-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Media First"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="profile-description">Description (optional)</Label>
          <Input
            id="profile-description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="Emphasize media coverage in the custom aggregate"
          />
        </div>
        {WEIGHT_FIELDS.map(([key, label]) => (
          <div key={key} className="grid gap-1 sm:grid-cols-[1fr_5.5rem] sm:items-center">
            <Label htmlFor={`weight-${key}`}>{label}</Label>
            <Input
              id={`weight-${key}`}
              type="number"
              min={0}
              max={100}
              step={1}
              value={weights[key]}
              onChange={(event) =>
                setWeights((current) => ({ ...current, [key]: Number(event.target.value) }))
              }
            />
            {Number(weights[key]) === 0 ? (
              <p className="text-xs text-muted-foreground sm:col-span-2">
                {label} will not affect your custom score.
              </p>
            ) : null}
          </div>
        ))}
        <div className="flex items-center justify-between border-t border-border pt-3 text-sm">
          <span>Total</span>
          <span className={cn("tabular-nums font-medium", total === 100 ? "" : "text-destructive")}>
            {total}%
          </span>
        </div>
        {total !== 100 ? (
          <p className="text-xs text-destructive">Weights must total 100. They are not auto-normalized.</p>
        ) : null}
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={setDefault}
            onChange={(event) => setSetDefault(event.target.checked)}
          />
          Use as organization default custom profile
        </label>
        {error ? (
          <Alert variant="destructive">
            <AlertTitle>Could not save profile</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="button"
            disabled={!valid || saving}
            onClick={async () => {
              setSaving(true);
              setError(null);
              try {
                const payload = {
                  name: name.trim(),
                  description: description.trim() || null,
                  weights,
                  is_default: setDefault,
                };
                const saved = existing
                  ? await updateScoringProfile(existing.id, payload)
                  : await createScoringProfile(payload);
                await onSaved(saved);
              } catch (err) {
                setError(err instanceof ProductLookupError ? err.message : "The profile could not be saved.");
              } finally {
                setSaving(false);
              }
            }}
          >
            {saving ? "Saving…" : "Save Profile"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function ManageProfilesModal({
  profiles,
  onClose,
  onChanged,
  onEdit,
}: {
  profiles: ScoringProfile[];
  onClose: () => void;
  onChanged: (nextSelected?: string) => Promise<void>;
  onEdit: (profile: ScoringProfile) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  return (
    <Modal title="Manage scoring profiles" onClose={onClose}>
      <p className="text-sm text-muted-foreground">
        Standard V2 cannot be edited or deleted. Archiving a custom profile does not change historical
        reports.
      </p>
      {profiles.length === 0 ? (
        <p className="text-sm text-muted-foreground">No custom profiles yet.</p>
      ) : (
        <ul className="divide-y divide-border">
          {profiles.map((profile) => (
            <li key={profile.id} className="flex flex-wrap items-center justify-between gap-2 py-3">
              <div>
                <p className="text-sm font-medium">{profile.name}</p>
                <p className="text-xs text-muted-foreground">
                  {profile.is_default ? "Organization default custom profile" : "Custom profile"}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button type="button" size="sm" variant="outline" onClick={() => onEdit(profile)}>
                  Edit weights
                </Button>
                {!profile.is_default ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={busyId === profile.id}
                    onClick={async () => {
                      setBusyId(profile.id);
                      setError(null);
                      try {
                        await updateScoringProfile(profile.id, { is_default: true });
                        await onChanged(profile.id);
                      } catch (err) {
                        setError(err instanceof ProductLookupError ? err.message : "Could not set default.");
                      } finally {
                        setBusyId(null);
                      }
                    }}
                  >
                    Set as default
                  </Button>
                ) : null}
                <Button
                  type="button"
                  size="sm"
                  variant="destructive"
                  disabled={busyId === profile.id}
                  onClick={async () => {
                    setBusyId(profile.id);
                    setError(null);
                    try {
                      await archiveScoringProfile(profile.id);
                      await onChanged();
                    } catch (err) {
                      setError(err instanceof ProductLookupError ? err.message : "Could not archive profile.");
                    } finally {
                      setBusyId(null);
                    }
                  }}
                >
                  Archive
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      <div className="flex justify-end">
        <Button type="button" variant="outline" onClick={onClose}>
          Close
        </Button>
      </div>
    </Modal>
  );
}

function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4 sm:items-center">
      <Panel className="w-full max-w-lg space-y-4 p-5">
        <div className="flex items-start justify-between gap-3">
          <h3 className="text-base font-semibold">{title}</h3>
          <Button type="button" size="sm" variant="ghost" onClick={onClose}>
            Close
          </Button>
        </div>
        {children}
      </Panel>
    </div>
  );
}

export function ScoringProfileSnapshotView({
  profileName,
  weights,
}: {
  profileName: string;
  weights: ScoringWeights;
}) {
  return (
    <Panel className="p-5">
      <h3 className="mb-1 text-[0.95rem] font-semibold">Custom Scoring Profile</h3>
      <p className="mb-4 text-sm text-muted-foreground">{profileName}</p>
      <p className="mb-4 text-xs text-muted-foreground">This profile snapshot is historical.</p>
      <dl className="space-y-2 text-sm">
        {WEIGHT_FIELDS.map(([key, label]) => (
          <div key={key} className="flex justify-between gap-3">
            <dt>{label}</dt>
            <dd className="tabular-nums">{weights[key]}%</dd>
          </div>
        ))}
      </dl>
    </Panel>
  );
}
