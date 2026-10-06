using System.Globalization;
using System.Net;
using System.Security.Cryptography;
using System.Text.RegularExpressions;
using UrlShortener.Api.Data;

namespace UrlShortener.Api.Services;

public sealed class LinkService(ILinkDataProvider dataProvider) : ILinkService
{
    private static readonly Regex AliasPattern = new("^[A-Za-z0-9_-]{4,32}$", RegexOptions.Compiled);

    public async Task<string> CreateAsync(string url, string? alias, long? ttlSeconds, CancellationToken cancellationToken = default)
    {
        ValidateUrl(url);
        if (alias is not null && !AliasPattern.IsMatch(alias))
            throw new ApiException(400, "Alias must contain 4-32 letters, digits, underscores or hyphens");
        if (ttlSeconds is not null && ttlSeconds is < 1 or > 31_536_000)
            throw new ApiException(400, "ttl_seconds must be between 1 and 31536000");

        for (var attempt = 0; attempt < 5; attempt++)
        {
            var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            var link = new LinkEntity
            {
                Code = alias ?? GenerateCode(),
                Url = url,
                Created = now,
                Expires = ttlSeconds is null ? null : now + ttlSeconds.Value
            };
            try
            {
                await dataProvider.AddLinkAsync(link, cancellationToken);
                return link.Code;
            }
            catch (LinkCodeConflictException)
            {
                if (alias is not null)
                    throw new ApiException(409, "Alias already exists");
            }
        }

        throw new ApiException(503, "Could not allocate code");
    }

    public async Task<string> ResolveAsync(string code, CancellationToken cancellationToken = default)
    {
        var result = await dataProvider.ResolveAndTrackAsync(
            code,
            DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
            DateTime.UtcNow.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture),
            cancellationToken);
        return result.Status switch
        {
            LinkResolutionStatus.Found => result.Url!,
            LinkResolutionStatus.NotFound => throw new ApiException(404, "Link not found"),
            _ => throw new ApiException(410, "Link expired or disabled")
        };
    }

    public async Task<LinkStats> GetStatsAsync(string code, CancellationToken cancellationToken = default)
    {
        var daily = await dataProvider.GetDailyClicksAsync(code, cancellationToken);
        if (daily is null)
            throw new ApiException(404, "Link not found");
        return new LinkStats(code, daily.Values.Sum(), daily);
    }

    public async Task DisableAsync(string code, CancellationToken cancellationToken = default)
    {
        if (!await dataProvider.DisableAsync(code, cancellationToken))
            throw new ApiException(404, "Link not found");
    }

    public Task<bool> CanConnectAsync(CancellationToken cancellationToken = default) =>
        dataProvider.CanConnectAsync(cancellationToken);

    private static void ValidateUrl(string url)
    {
        if (url.Length > 2048 || url.Any(character => character < 33) ||
            !Uri.TryCreate(url, UriKind.Absolute, out var uri) ||
            (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps) ||
            string.IsNullOrWhiteSpace(uri.Host) || uri.UserInfo.Length > 0)
            throw new ApiException(400, "Use a public HTTP(S) URL without credentials");

        var host = uri.Host.TrimEnd('.').ToLowerInvariant();
        if (host == "localhost" || host.EndsWith(".local", StringComparison.Ordinal) ||
            host.EndsWith(".localhost", StringComparison.Ordinal) || !host.Contains('.'))
            throw new ApiException(400, "Use a public HTTP(S) URL without credentials");

        if (IPAddress.TryParse(host, out var address) && !IsPublicAddress(address))
            throw new ApiException(400, "Use a public HTTP(S) URL without credentials");
    }

    private static bool IsPublicAddress(IPAddress address)
    {
        if (address.IsIPv4MappedToIPv6)
            address = address.MapToIPv4();
        var bytes = address.GetAddressBytes();
        if (address.AddressFamily == System.Net.Sockets.AddressFamily.InterNetwork)
        {
            var first = bytes[0];
            var second = bytes[1];
            return first != 0 && first != 10 && first != 127 && first < 224 &&
                   !(first == 100 && second is >= 64 and <= 127) &&
                   !(first == 169 && second == 254) &&
                   !(first == 172 && second is >= 16 and <= 31) &&
                   !(first == 192 && (second == 0 || second == 168)) &&
                   !(first == 198 && (second == 18 || second == 19 || second == 51)) &&
                   !(first == 203 && second == 0 && bytes[2] == 113);
        }

        if (address.AddressFamily != System.Net.Sockets.AddressFamily.InterNetworkV6 || bytes[0] is < 0x20 or > 0x3f)
            return false;
        return !(bytes[0] == 0x20 && bytes[1] == 0x01 && bytes[2] == 0x0d && bytes[3] == 0xb8);
    }

    private static string GenerateCode() => Convert.ToBase64String(RandomNumberGenerator.GetBytes(6))
        .TrimEnd('=').Replace('+', '-').Replace('/', '_');
}

public sealed class ApiException(int statusCode, string message) : Exception(message)
{
    public int StatusCode { get; } = statusCode;
}