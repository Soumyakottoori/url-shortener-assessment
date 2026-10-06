namespace UrlShortener.Api.Services;

public interface ILinkService
{
    Task<string> CreateAsync(string url, string? alias, long? ttlSeconds, CancellationToken cancellationToken = default);
    Task<string> ResolveAsync(string code, CancellationToken cancellationToken = default);
    Task<LinkStats> GetStatsAsync(string code, CancellationToken cancellationToken = default);
    Task DisableAsync(string code, CancellationToken cancellationToken = default);
    Task<bool> CanConnectAsync(CancellationToken cancellationToken = default);
}