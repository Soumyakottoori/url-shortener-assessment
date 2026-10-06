using Microsoft.EntityFrameworkCore;

namespace UrlShortener.Api.Data;

public sealed class InMemoryLinkDataProvider(
    IDbContextFactory<ShortenerDbContext> contextFactory,
    InMemoryWriteLock writeLock) : ILinkDataProvider
{
    public async Task InitializeAsync(CancellationToken cancellationToken = default)
    {
        await using var database = await contextFactory.CreateDbContextAsync(cancellationToken);
        await database.Database.EnsureCreatedAsync(cancellationToken);
    }

    public async Task AddLinkAsync(LinkEntity link, CancellationToken cancellationToken = default)
    {
        await writeLock.Gate.WaitAsync(cancellationToken);
        try
        {
            await using var database = await contextFactory.CreateDbContextAsync(cancellationToken);
            if (await database.Links.AnyAsync(item => item.Code == link.Code, cancellationToken))
                throw new LinkCodeConflictException();
            database.Links.Add(link);
            await database.SaveChangesAsync(cancellationToken);
        }
        finally
        {
            writeLock.Gate.Release();
        }
    }

    public async Task<LinkResolution> ResolveAndTrackAsync(string code, long now, string utcDay, CancellationToken cancellationToken = default)
    {
        await writeLock.Gate.WaitAsync(cancellationToken);
        try
        {
            await using var database = await contextFactory.CreateDbContextAsync(cancellationToken);
            var link = await database.Links.SingleOrDefaultAsync(item => item.Code == code, cancellationToken);
            if (link is null)
                return new LinkResolution(LinkResolutionStatus.NotFound, null);
            if (link.Disabled || (link.Expires is not null && link.Expires <= now))
                return new LinkResolution(LinkResolutionStatus.ExpiredOrDisabled, null);

            var click = await database.Clicks.SingleOrDefaultAsync(
                item => item.Code == code && item.Day == utcDay,
                cancellationToken);
            if (click is null)
                database.Clicks.Add(new ClickEntity { Code = code, Day = utcDay, Count = 1 });
            else
                click.Count++;
            await database.SaveChangesAsync(cancellationToken);
            return new LinkResolution(LinkResolutionStatus.Found, link.Url);
        }
        finally
        {
            writeLock.Gate.Release();
        }
    }

    public async Task<IReadOnlyDictionary<string, long>?> GetDailyClicksAsync(string code, CancellationToken cancellationToken = default)
    {
        await using var database = await contextFactory.CreateDbContextAsync(cancellationToken);
        if (!await database.Links.AnyAsync(link => link.Code == code, cancellationToken))
            return null;

        return await database.Clicks
            .Where(click => click.Code == code)
            .OrderBy(click => click.Day)
            .ToDictionaryAsync(click => click.Day, click => click.Count, cancellationToken);
    }

    public async Task<bool> DisableAsync(string code, CancellationToken cancellationToken = default)
    {
        await writeLock.Gate.WaitAsync(cancellationToken);
        try
        {
            await using var database = await contextFactory.CreateDbContextAsync(cancellationToken);
            var link = await database.Links.SingleOrDefaultAsync(item => item.Code == code, cancellationToken);
            if (link is null)
                return false;
            link.Disabled = true;
            await database.SaveChangesAsync(cancellationToken);
            return true;
        }
        finally
        {
            writeLock.Gate.Release();
        }
    }

    public async Task<bool> CanConnectAsync(CancellationToken cancellationToken = default)
    {
        await using var database = await contextFactory.CreateDbContextAsync(cancellationToken);
        return await database.Database.CanConnectAsync(cancellationToken);
    }
}

public sealed class InMemoryWriteLock
{
    public SemaphoreSlim Gate { get; } = new(1, 1);
}