namespace UrlShortener.Api.Data;

public interface ILinkDataProvider
{
    Task InitializeAsync(CancellationToken cancellationToken = default);
    Task AddLinkAsync(LinkEntity link, CancellationToken cancellationToken = default);
    Task<LinkResolution> ResolveAndTrackAsync(string code, long now, string utcDay, CancellationToken cancellationToken = default);
    Task<IReadOnlyDictionary<string, long>?> GetDailyClicksAsync(string code, CancellationToken cancellationToken = default);
    Task<bool> DisableAsync(string code, CancellationToken cancellationToken = default);
    Task<bool> CanConnectAsync(CancellationToken cancellationToken = default);
}