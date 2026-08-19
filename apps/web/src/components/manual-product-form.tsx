"use client";

import { useState, type FormEvent } from "react";
import { Loader2, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { createManualProduct, ProductLookupError } from "@/lib/api";
import { isValidAsin, normalizeAsin } from "@/lib/asin";
import type { ProductResponse } from "@/lib/types";

const MAX_BULLETS = 10;
const MAX_IMAGES = 8;

function parseOptionalNumber(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  return Number(trimmed);
}

type ManualProductFormProps = {
  loading: boolean;
  onLoadingChange: (loading: boolean) => void;
  onSuccess: (result: ProductResponse) => void;
  onError: (message: string | null) => void;
};

export function ManualProductForm({
  loading,
  onLoadingChange,
  onSuccess,
  onError,
}: ManualProductFormProps) {
  const [asin, setAsin] = useState("");
  const [title, setTitle] = useState("");
  const [brand, setBrand] = useState("");
  const [price, setPrice] = useState("");
  const [rating, setRating] = useState("");
  const [reviewCount, setReviewCount] = useState("");
  const [category, setCategory] = useState("");
  const [bsrRank, setBsrRank] = useState("");
  const [bsrCategory, setBsrCategory] = useState("");
  const [availability, setAvailability] = useState("");
  const [seller, setSeller] = useState("");
  const [description, setDescription] = useState("");
  const [bullets, setBullets] = useState<string[]>([""]);
  const [imageUrls, setImageUrls] = useState<string[]>([""]);
  const [marketplace] = useState("amazon.in");

  function updateBullet(index: number, value: string) {
    setBullets((current) => current.map((item, i) => (i === index ? value : item)));
  }

  function updateImage(index: number, value: string) {
    setImageUrls((current) => current.map((item, i) => (i === index ? value : item)));
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedAsin = normalizeAsin(asin);

    if (!isValidAsin(normalizedAsin)) {
      onError("Enter a valid 10-character ASIN using letters and numbers only.");
      return;
    }
    if (!title.trim()) {
      onError("Title is required.");
      return;
    }

    const parsedPrice = parseOptionalNumber(price);
    const parsedRating = parseOptionalNumber(rating);
    const parsedReviews = parseOptionalNumber(reviewCount);
    const parsedBsr = parseOptionalNumber(bsrRank);

    if (price.trim() && (parsedPrice == null || Number.isNaN(parsedPrice) || parsedPrice < 0)) {
      onError("Price must be a number that is 0 or greater.");
      return;
    }
    if (
      rating.trim() &&
      (parsedRating == null || Number.isNaN(parsedRating) || parsedRating < 0 || parsedRating > 5)
    ) {
      onError("Rating must be a number between 0 and 5.");
      return;
    }
    if (
      reviewCount.trim() &&
      (parsedReviews == null ||
        Number.isNaN(parsedReviews) ||
        parsedReviews < 0 ||
        !Number.isInteger(parsedReviews))
    ) {
      onError("Review count must be a whole number that is 0 or greater.");
      return;
    }
    if (bsrRank.trim() && (parsedBsr == null || Number.isNaN(parsedBsr) || parsedBsr < 1)) {
      onError("Best Seller Rank must be a whole number of 1 or greater.");
      return;
    }

    onLoadingChange(true);
    onError(null);

    try {
      const result = await createManualProduct({
        asin: normalizedAsin,
        title: title.trim(),
        brand: brand.trim() || null,
        price: parsedPrice,
        currency: "INR",
        rating: parsedRating,
        review_count: parsedReviews,
        category: category.trim() || null,
        bsr_rank: parsedBsr,
        bsr_category: bsrCategory.trim() || null,
        availability: availability.trim() || null,
        seller: seller.trim() || null,
        description: description.trim() || null,
        bullet_points: bullets.map((item) => item.trim()).filter(Boolean),
        image_urls: imageUrls.map((item) => item.trim()).filter(Boolean),
        marketplace,
      });
      onSuccess(result);
    } catch (err) {
      if (err instanceof ProductLookupError) {
        onError(err.message);
      } else {
        onError("Something went wrong. Please try again.");
      }
    } finally {
      onLoadingChange(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="manual-asin">ASIN</Label>
          <Input
            id="manual-asin"
            value={asin}
            onChange={(event) => setAsin(event.target.value.toUpperCase())}
            placeholder="B0XXXXXXXX"
            autoComplete="off"
            spellCheck={false}
            className="font-mono tracking-wide"
            required
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="manual-marketplace">Marketplace</Label>
          <Input id="manual-marketplace" value={marketplace} readOnly />
        </div>
        <div className="space-y-2 sm:col-span-2">
          <Label htmlFor="manual-title">Title</Label>
          <Input
            id="manual-title"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="Product title as shown on Amazon"
            required
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="manual-brand">Brand</Label>
          <Input
            id="manual-brand"
            value={brand}
            onChange={(event) => setBrand(event.target.value)}
            placeholder="Optional"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="manual-price">Price (INR)</Label>
          <Input
            id="manual-price"
            inputMode="decimal"
            value={price}
            onChange={(event) => setPrice(event.target.value)}
            placeholder="449"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="manual-rating">Rating</Label>
          <Input
            id="manual-rating"
            inputMode="decimal"
            value={rating}
            onChange={(event) => setRating(event.target.value)}
            placeholder="4.4"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="manual-reviews">Review count</Label>
          <Input
            id="manual-reviews"
            inputMode="numeric"
            value={reviewCount}
            onChange={(event) => setReviewCount(event.target.value)}
            placeholder="1284"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="manual-category">Category</Label>
          <Input
            id="manual-category"
            value={category}
            onChange={(event) => setCategory(event.target.value)}
            placeholder="Health & Personal Care"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="manual-bsr">Best Seller Rank</Label>
          <Input
            id="manual-bsr"
            inputMode="numeric"
            value={bsrRank}
            onChange={(event) => setBsrRank(event.target.value)}
            placeholder="1842"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="manual-bsr-category">BSR category</Label>
          <Input
            id="manual-bsr-category"
            value={bsrCategory}
            onChange={(event) => setBsrCategory(event.target.value)}
            placeholder="Optional"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="manual-availability">Availability</Label>
          <Input
            id="manual-availability"
            value={availability}
            onChange={(event) => setAvailability(event.target.value)}
            placeholder="In Stock"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="manual-seller">Seller</Label>
          <Input
            id="manual-seller"
            value={seller}
            onChange={(event) => setSeller(event.target.value)}
            placeholder="Store name"
          />
        </div>
        <div className="space-y-2 sm:col-span-2">
          <Label htmlFor="manual-description">Description</Label>
          <Textarea
            id="manual-description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="Optional product description"
          />
        </div>
      </div>

      <div className="space-y-3">
        <Label>Bullet points</Label>
        {bullets.map((bullet, index) => (
          <div key={`bullet-${index}`} className="flex gap-2">
            <Input
              value={bullet}
              onChange={(event) => updateBullet(index, event.target.value)}
              placeholder={`Bullet point ${index + 1}`}
            />
            {bullets.length > 1 ? (
              <Button
                type="button"
                variant="outline"
                size="icon"
                aria-label={`Remove bullet point ${index + 1}`}
                onClick={() => setBullets((current) => current.filter((_, i) => i !== index))}
              >
                <Trash2 />
              </Button>
            ) : null}
          </div>
        ))}
        {bullets.length < MAX_BULLETS ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setBullets((current) => [...current, ""])}
          >
            <Plus />
            Add Bullet Point
          </Button>
        ) : null}
      </div>

      <div className="space-y-3">
        <Label>Image URLs</Label>
        {imageUrls.map((url, index) => (
          <div key={`image-${index}`} className="flex gap-2">
            <Input
              value={url}
              onChange={(event) => updateImage(index, event.target.value)}
              placeholder="https://"
            />
            {imageUrls.length > 1 ? (
              <Button
                type="button"
                variant="outline"
                size="icon"
                aria-label={`Remove image URL ${index + 1}`}
                onClick={() => setImageUrls((current) => current.filter((_, i) => i !== index))}
              >
                <Trash2 />
              </Button>
            ) : null}
          </div>
        ))}
        {imageUrls.length < MAX_IMAGES ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setImageUrls((current) => [...current, ""])}
          >
            <Plus />
            Add Image
          </Button>
        ) : null}
      </div>

      <Button type="submit" size="lg" disabled={loading} className="w-full sm:w-auto">
        {loading ? (
          <>
            <Loader2 className="animate-spin" />
            Analyzing…
          </>
        ) : (
          "Analyze Product"
        )}
      </Button>
    </form>
  );
}
