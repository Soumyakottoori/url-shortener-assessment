using System.ComponentModel.DataAnnotations;
using System.Text.Json.Serialization;

namespace UrlShortener.Api;

public sealed class CreateLinkRequest
{
    [Required]
    [StringLength(2048, MinimumLength = 1)]
    public string Url { get; init; } = "";

    [RegularExpression("^[A-Za-z0-9_-]{4,32}$")]
    public string? Alias { get; init; }

    [Range(1, 31_536_000)]
    [JsonPropertyName("ttl_seconds")]
    public long? TtlSeconds { get; init; }
}

public sealed record CreateLinkResponse(string Code, [property: JsonPropertyName("short_url")] string ShortUrl);
public sealed record ErrorResponse(string Error);
public sealed record LinkStats(
    string Code,
    [property: JsonPropertyName("total_clicks")] long TotalClicks,
    IReadOnlyDictionary<string, long> Daily);