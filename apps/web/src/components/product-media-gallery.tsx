"use client";

import { useState } from "react";
import { Play } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { Product, ProductVideo } from "@/lib/types";
import { cn } from "@/lib/utils";

function MediaFrame({
  src,
  alt,
  className,
  onFail,
}: {
  src: string;
  alt: string;
  className?: string;
  onFail?: () => void;
}) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div
        className={cn(
          "flex items-center justify-center bg-muted text-sm text-muted-foreground",
          className,
        )}
      >
        Image unavailable
      </div>
    );
  }
  return (
    // Remote Amazon CDN URLs vary by listing; next/image host allowlists are too brittle.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt={alt}
      className={cn("max-h-full max-w-full object-contain", className)}
      onError={() => {
        setFailed(true);
        onFail?.();
      }}
    />
  );
}

export function ProductMediaGallery({ product }: { product: Product }) {
  const images = product.images ?? [];
  const videos = product.videos ?? [];
  const [activeIndex, setActiveIndex] = useState(0);
  const [broken, setBroken] = useState<Set<string>>(() => new Set());

  const visible = images.filter((image) => !broken.has(image.url));
  const active = visible[Math.min(activeIndex, Math.max(visible.length - 1, 0))] ?? null;

  function markBroken(url: string) {
    setBroken((current) => new Set(current).add(url));
  }

  if (!visible.length && !videos.length) {
    return <p className="text-sm text-muted-foreground">Not available</p>;
  }

  return (
    <div className="space-y-6">
      {visible.length ? (
        <div className="flex flex-col gap-4 sm:flex-row">
          {visible.length > 1 ? (
            <div className="flex gap-2 overflow-x-auto sm:max-h-[28rem] sm:w-20 sm:flex-col sm:overflow-y-auto">
              {visible.map((image, index) => (
                <button
                  key={image.url}
                  type="button"
                  onClick={() => setActiveIndex(index)}
                  className={cn(
                    "h-16 w-16 shrink-0 overflow-hidden rounded-md border bg-muted",
                    index === activeIndex ? "border-primary ring-1 ring-primary" : "border-border",
                  )}
                  aria-label={`Show image ${index + 1}`}
                >
                  <MediaFrame
                    src={image.url}
                    alt={image.alt ?? product.title}
                    className="h-full w-full"
                    onFail={() => markBroken(image.url)}
                  />
                </button>
              ))}
            </div>
          ) : null}

          <div className="flex min-h-64 flex-1 items-center justify-center rounded-lg border border-border bg-muted/40 p-4 sm:min-h-[28rem]">
            {active ? (
              <MediaFrame
                src={active.url}
                alt={active.alt ?? product.title}
                className="max-h-64 sm:max-h-[26rem]"
                onFail={() => markBroken(active.url)}
              />
            ) : (
              <p className="text-sm text-muted-foreground">Image unavailable</p>
            )}
          </div>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">Image unavailable</p>
      )}

      {videos.length ? (
        <div className="space-y-3">
          <h3 className="text-sm font-medium">Videos</h3>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {videos.map((video, index) => (
              <VideoCard key={`${video.video_url ?? video.thumbnail_url ?? index}`} video={video} />
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function VideoCard({ video }: { video: ProductVideo }) {
  const [playing, setPlaying] = useState(false);
  const label = video.title?.trim() || "Video";

  if (playing && video.video_url) {
    return (
      <div className="overflow-hidden rounded-lg border border-border bg-black">
        <video
          src={video.video_url}
          poster={video.thumbnail_url ?? undefined}
          controls
          autoPlay
          className="aspect-video w-full"
        >
          This browser cannot play the video.
        </video>
      </div>
    );
  }

  const inner = (
    <div className="relative flex aspect-video items-center justify-center overflow-hidden rounded-lg border border-border bg-muted">
      {video.thumbnail_url ? (
        <MediaFrame src={video.thumbnail_url} alt={label} className="h-full w-full" />
      ) : (
        <span className="text-sm text-muted-foreground">Video</span>
      )}
      <div className="absolute inset-0 flex items-center justify-center bg-black/25">
        <span className="flex items-center gap-2 rounded-full bg-black/70 px-3 py-1.5 text-xs font-medium text-white">
          <Play className="size-3.5 fill-white" />
          Video
        </span>
      </div>
      {video.duration_seconds ? (
        <Badge className="absolute bottom-2 right-2" variant="secondary">
          {video.duration_seconds}s
        </Badge>
      ) : null}
    </div>
  );

  if (video.video_url) {
    return (
      <button type="button" className="block w-full text-left" onClick={() => setPlaying(true)}>
        {inner}
        <p className="mt-2 text-sm">{label}</p>
      </button>
    );
  }

  return (
    <div>
      {inner}
      <p className="mt-2 text-sm">{label}</p>
      <p className="text-xs text-muted-foreground">Playable video URL was not provided.</p>
    </div>
  );
}
