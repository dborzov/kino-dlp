The scanners and metadata agents used by Plex will work best when your major types of content are separated from each other. We *strongly* recommend separating movie and television content into separate main directories. For instance, you might use something like this:

```js
/Media
   /Movies
      movie content
   /Music
      music content
   /TV Shows
      television content
```

**Warning!**: Plex will do its best to appropriately find and match the content. However, a failure to separate content such as movies and TV shows may result in unexpected or incorrect behavior.

In the above example, it is the main folder of each type of content (e.g. `/Movies`, `/Music`, `/TV Shows`) that you would typically specify as the content location for that library type.

**Tip!**: More specifically for television content, the folder you want to specify as the content location for the library is the folder that contains each of the individual show folders. So, if you chose to categorize your children’s content separate from more “adult” content (e.g. `/TV Shows/Kids/ShowName` vs `/TV Shows/Regular/ShowName`), then you would specify `/TV Shows/Kids` as the source location for a “kids” TV library.

TV shows can be season-based, date-based, a miniseries, or more. Both the folder structure and each episode filename must be correct for the best matching experience. If you’re not sure whether a show is season- or date-based, check [The Movie Database](https://www.themoviedb.org/) (TMDB) or [The TVDB](http://thetvdb.com/) and name it as it appears there.

Examples of the file naming/organization mentioned can be found at the end of the article.

### Some important notes:

- For the “Plex TV Series” agent, it is recommended to always include the year alongside the series title in folder and file names, e.g. `/Band of Brothers (2001)/Season 01/Band of Brothers (2001) - s01e01 - Currahee.mkv`
- Be sure to use the English word “Season” when creating season directories, even if your content is in another language.
- Many of our naming instructions mention having `Optional_Info` at the end of the file name. As the label suggests, it’s optional, but many people like to use it for information about the files in question. Such optional info is ignored by Plex when matching content with legacy agents, but it is used in the Plex TV Series agent to give a hint for matching. If you want info to be ignored put the optional info in brackets. e.g. `/Band of Brothers (2001) - s01e01 - Currahee [1080p Bluray].mkv`
- We use `.ext` as a generic file extension in the naming/organizing instructions. You should use the appropriate file extension for your files, of course. (Some operating systems such as Windows may [hide your file extensions](http://www.thewindowsclub.com/show-file-extensions-in-windows) by default.)
- If you are using the “Plex TV Series” agent, you can optionally include the TMDB, TVDB, or IMDb show ID in the folder name to improve matching. If you choose to do that, it must be inside curly braces: `ShowName (2020) {tmdb-123456}`, `ShowName (2020) {tvdb-123456}`, or `ShowName (2020) {imdb-tt123456}`, where `123456` is the show ID. An example can be found at the end of the article.
- As an alternative, you can also use a [.plexmatch file](https://support.plex.tv/articles/plexmatch/)

## Episode Ordering

Some shows can have episodes in different orders, depending on where they were originally aired, how they were packaged (a DVD/Blu-ray vs the original broadcast airing), or other reasons. For instance, some people like to handle anime series as a single season, using “Absolute” series order. Sometimes the two main episode reference sites ([The Movie Database](https://www.themoviedb.org/) (TMDB) or [The TVDB](http://thetvdb.com/)) can have differences in the “aired” order they use.

### Setting the Library Default Ordering

By default, the **Plex TV Series** agent uses the episode ordering based on TMDB, which is an aired order. However, if you know that your files are named according TVDB, you can change the Episode Ordering preference when creating or editing your TV library. This is available under the Advanced tab, when creating or editing the television library.

### Alternate orders

As of Plex Media Server version 1.40.4 (and when using the **Plex TV Series** agent), it is possible to set the appropriate episode order for a TV series (based on alternate orders available from The TVDB for that series), after it has been successfully matched. This is available from the Advanced tab, when editing the TV show.

**Notes:** If you do not see the orders for a specific show as seen on TheTVDB website for that show then you likely just need to refresh metadata for your library or that individual show. Also if an order was recently created (usually within 48hours) we may not have all the info for it even if the order is displayed in menu.

For example, the show *Iron Chef* had a different order in the United States. In the screenshot below, we can see the different options available from the Episode ordering preference, when editing that show. In this case, the episode files are named and organized based on that US airing release and so `TheTVDB (US)` is chosen as the order.

![](https://support.plex.tv/wp-content/uploads/sites/4/2019/05/Plex-3.png)

Shows will most commonly have one or more of these options for episode order:

- The Movie Database (Aired)
- TheTVDB (Aired)
- TheTVDB (DVD)
- TheTVDB (Absolute)

## Standard, Season-Based Shows

Most television shows have episodes organized into seasons. To name season-based shows, use files with the season and episode notation `sXXeXX`:

- /TV Shows/ShowName/Season 02/ShowName – s02e17 – Optional\_Info.ext

This is only an example. The most important bit in the file name is the appropriate season and episode number notation **s02e17,** which in this example means Season 2 Episode 17 It does not matter if you use dashes, dots, or just spaces.

## Date-Based Television Shows

TV Shows that are date-based should be named as follows:

- /TV Shows/ShowName/Season 02/ShowName – 2011-11-15 – Optional\_Info.ext
- /TV Shows/ShowName/Season 02/ShowName – 15-11-2011 – Optional\_Info.ext

Where you specify the appropriate date. The date can use either the YYYY-MM-DD or DD-MM-YYYY formats and can use different separators:

- Dashes (2011-11-15)
- Periods (2011.11.15)
- Spaces (2011 11 15)

## Miniseries

A miniseries is really handled just like a season-based show, you simply always use “Season 01” as the season.

## Television Specials

Shows sometimes air “specials” or other content that isn’t part of the standard season. “Specials” episodes are always part of season zero (i.e. season number “00”) and should be placed inside a folder named either `Season 00` or `Specials`.

- /TV Shows/ShowName/Specials/ShowName – s00e13 – Optional\_Info.ext

Where you specify the correct episode numbers. If you’re unsure whether a particular episode is a Special or not, check the episode on [TMDB](https://www.themoviedb.org/ "TheTVDB Website") and name it as you see it there.

If an special you have doesn’t appear in [TMDB](https://www.themoviedb.org/ "TheTVDB Website") (e.g. it’s a DVD special, behind the scenes, goof reel, etc.), you can instead add the item as an “extra” for the show. See our [article about TV extras](https://support.plex.tv/articles/local-files-for-tv-show-trailers-and-extras/).

**Related Page**: [Local Files for TV Show Trailers and Extras](https://support.plex.tv/articles/local-files-for-tv-show-trailers-and-extras/)

## Multiple Episodes in a Single File

If a single file covers more than one episode, name it using the following format:

- /TV Shows/ShowName/Season 02/ShowName – sXXeYY-eZZ – Optional\_Info.ext

Where you specify the appropriate season, episode numbers (the first and last episode covered in the file), and file extension. For example, `s02e18-e19`.

**Note**: Multi-episode files will show up individually in Plex apps when viewing your library, but playing any of the represented episodes will play the full file. If you want episodes to behave truly independently, you’re best off using a tool to split the file into individual episodes.

To get a better overall experience, we recommend that you use a tool to split the video so that each episode has its own individual file. There are multiple ways you can do this and a quick search in your favorite search engine should give you some options on how to “split” a file. An unofficial guide with one free tool has even been posted in our forums.

**Related Page**: [Forums: Splitting multi-episode files with MKVtoolnix GUI](https://forums.plex.tv/t/howto-splitting-multi-episode-files-with-mkvtoolnix-gui/108396/1)

## Episodes Split Across Multiple Files

**Warning!**: While Plex does have limited support for content split across multiple files, it is not the expected way to handle content. Doing this may negatively impact usage of various Plex features (including, but not limited to, preview thumbnails, skip intro, audio/subtitle stream selection across parts, and more). We recommend users instead join the files together (see below).

Episodes that are split into several files (e.g. pt1, pt2), can be played back as a single file if named correctly. Name the files as follows:

- /TV Shows/ShowName/Season 02/ShowName – s02e17 – Split\_Name.ext

Where `Split_Name` is one of the following:

- cdX
- discX
- diskX
- dvdX
- partX
- ptX

…and you replace `X` with the appropriate number (cd1, cd2, etc.).

**Notes**:

- Not all Plex apps support playback of media split across multiple files
- All parts must be of the same file format (e.g. all MP4 or all MKV)
- All parts should have identical audio and subtitle streams in the same order
- Only stacks up to 8 parts are supported
- Not all features will work correctly when using “split” files.

To get a better overall experience, *we strongly encourage* you to instead use a tool to join/merge the individual files into a single video. There are multiple ways you can do this and a quick search in your favorite search engine should give you some options on how to “join” files. An unofficial guide with one free tool has even been posted in our forums.

**Related Page**: [Forums: Joining multi-part movie files with MKVtoolnix GUI](https://forums.plex.tv/t/howto-joining-multi-part-movies-files-with-mkvtoolnix-gui/113211/1)

## Examples

**Note**: This example illustrates many of the types of content outlined previously. When creating the TV library, it is the `/TV Shows` directory that would be specified as the content location for the library.

```js
/TV Shows
   /Doctor Who (1963)
      /Season 01
         Doctor Who (1963) - s01e01 - An Unearthly Child (1).mp4
         Doctor Who (1963) - s01e02 - The Cave of Skulls (2).mp4
   /From the Earth to the Moon (1998)
      /Season 01
         From the Earth to the Moon (1998) - s01e01.mp4
         From the Earth to the Moon (1998) - s01e02.mp4
   /Grey's Anatomy (2005)
      /Season 00
         Grey's Anatomy (2005) - s00e01 - Straight to the Heart.mkv
      /Season 01
         Grey's Anatomy (2005) - s01e01 - pt1.avi
         Grey's Anatomy (2005) - s01e01 - pt2.avi
         Grey's Anatomy (2005) - s01e02 - The First Cut is the Deepest.avi
         Grey's Anatomy (2005) - s01e03.mp4
      /Season 02
         Grey's Anatomy (2005) - s02e01-e03.avi
         Grey's Anatomy (2005) - s02e04.m4v
   /The Colbert Report (2005)
      /Season 08
         The Colbert Report (2005) - 2011-11-15 - Elijah Wood.avi
   /The Office (UK) (2001) {tmdb-2996}
      /Season 01
         The Office (UK) - s01e01 - Downsize.mp4
   / The Office (US) (2005) {tvdb-73244}
      /Season 01
         The Office (US) - s01e01 - Pilot.mkv
```---
title: "Naming and Organizing Your TV Show Files"
source: "https://support.plex.tv/articles/naming-and-organizing-your-tv-show-files/"
author:
published: 2019-05-21
created: 2026-04-04
description: "The scanners and metadata agents used by Plex will work best when your major types of content are separated from..."
tags:
  - "clippings"
---
The scanners and metadata agents used by Plex will work best when your major types of content are separated from each other. We *strongly* recommend separating movie and television content into separate main directories. For instance, you might use something like this:

```js
/Media
   /Movies
      movie content
   /Music
      music content
   /TV Shows
      television content
```

**Warning!**: Plex will do its best to appropriately find and match the content. However, a failure to separate content such as movies and TV shows may result in unexpected or incorrect behavior.

In the above example, it is the main folder of each type of content (e.g. `/Movies`, `/Music`, `/TV Shows`) that you would typically specify as the content location for that library type.

**Tip!**: More specifically for television content, the folder you want to specify as the content location for the library is the folder that contains each of the individual show folders. So, if you chose to categorize your children’s content separate from more “adult” content (e.g. `/TV Shows/Kids/ShowName` vs `/TV Shows/Regular/ShowName`), then you would specify `/TV Shows/Kids` as the source location for a “kids” TV library.

TV shows can be season-based, date-based, a miniseries, or more. Both the folder structure and each episode filename must be correct for the best matching experience. If you’re not sure whether a show is season- or date-based, check [The Movie Database](https://www.themoviedb.org/) (TMDB) or [The TVDB](http://thetvdb.com/) and name it as it appears there.

Examples of the file naming/organization mentioned can be found at the end of the article.

### Some important notes:

- For the “Plex TV Series” agent, it is recommended to always include the year alongside the series title in folder and file names, e.g. `/Band of Brothers (2001)/Season 01/Band of Brothers (2001) - s01e01 - Currahee.mkv`
- Be sure to use the English word “Season” when creating season directories, even if your content is in another language.
- Many of our naming instructions mention having `Optional_Info` at the end of the file name. As the label suggests, it’s optional, but many people like to use it for information about the files in question. Such optional info is ignored by Plex when matching content with legacy agents, but it is used in the Plex TV Series agent to give a hint for matching. If you want info to be ignored put the optional info in brackets. e.g. `/Band of Brothers (2001) - s01e01 - Currahee [1080p Bluray].mkv`
- We use `.ext` as a generic file extension in the naming/organizing instructions. You should use the appropriate file extension for your files, of course. (Some operating systems such as Windows may [hide your file extensions](http://www.thewindowsclub.com/show-file-extensions-in-windows) by default.)
- If you are using the “Plex TV Series” agent, you can optionally include the TMDB, TVDB, or IMDb show ID in the folder name to improve matching. If you choose to do that, it must be inside curly braces: `ShowName (2020) {tmdb-123456}`, `ShowName (2020) {tvdb-123456}`, or `ShowName (2020) {imdb-tt123456}`, where `123456` is the show ID. An example can be found at the end of the article.
- As an alternative, you can also use a [.plexmatch file](https://support.plex.tv/articles/plexmatch/)

## Episode Ordering

Some shows can have episodes in different orders, depending on where they were originally aired, how they were packaged (a DVD/Blu-ray vs the original broadcast airing), or other reasons. For instance, some people like to handle anime series as a single season, using “Absolute” series order. Sometimes the two main episode reference sites ([The Movie Database](https://www.themoviedb.org/) (TMDB) or [The TVDB](http://thetvdb.com/)) can have differences in the “aired” order they use.

### Setting the Library Default Ordering

By default, the **Plex TV Series** agent uses the episode ordering based on TMDB, which is an aired order. However, if you know that your files are named according TVDB, you can change the Episode Ordering preference when creating or editing your TV library. This is available under the Advanced tab, when creating or editing the television library.

### Alternate orders

As of Plex Media Server version 1.40.4 (and when using the **Plex TV Series** agent), it is possible to set the appropriate episode order for a TV series (based on alternate orders available from The TVDB for that series), after it has been successfully matched. This is available from the Advanced tab, when editing the TV show.

**Notes:** If you do not see the orders for a specific show as seen on TheTVDB website for that show then you likely just need to refresh metadata for your library or that individual show. Also if an order was recently created (usually within 48hours) we may not have all the info for it even if the order is displayed in menu.

For example, the show *Iron Chef* had a different order in the United States. In the screenshot below, we can see the different options available from the Episode ordering preference, when editing that show. In this case, the episode files are named and organized based on that US airing release and so `TheTVDB (US)` is chosen as the order.

![](https://support.plex.tv/wp-content/uploads/sites/4/2019/05/Plex-3.png)

Shows will most commonly have one or more of these options for episode order:

- The Movie Database (Aired)
- TheTVDB (Aired)
- TheTVDB (DVD)
- TheTVDB (Absolute)

## Standard, Season-Based Shows

Most television shows have episodes organized into seasons. To name season-based shows, use files with the season and episode notation `sXXeXX`:

- /TV Shows/ShowName/Season 02/ShowName – s02e17 – Optional\_Info.ext

This is only an example. The most important bit in the file name is the appropriate season and episode number notation **s02e17,** which in this example means Season 2 Episode 17 It does not matter if you use dashes, dots, or just spaces.

## Date-Based Television Shows

TV Shows that are date-based should be named as follows:

- /TV Shows/ShowName/Season 02/ShowName – 2011-11-15 – Optional\_Info.ext
- /TV Shows/ShowName/Season 02/ShowName – 15-11-2011 – Optional\_Info.ext

Where you specify the appropriate date. The date can use either the YYYY-MM-DD or DD-MM-YYYY formats and can use different separators:

- Dashes (2011-11-15)
- Periods (2011.11.15)
- Spaces (2011 11 15)

## Miniseries

A miniseries is really handled just like a season-based show, you simply always use “Season 01” as the season.

## Television Specials

Shows sometimes air “specials” or other content that isn’t part of the standard season. “Specials” episodes are always part of season zero (i.e. season number “00”) and should be placed inside a folder named either `Season 00` or `Specials`.

- /TV Shows/ShowName/Specials/ShowName – s00e13 – Optional\_Info.ext

Where you specify the correct episode numbers. If you’re unsure whether a particular episode is a Special or not, check the episode on [TMDB](https://www.themoviedb.org/ "TheTVDB Website") and name it as you see it there.

If an special you have doesn’t appear in [TMDB](https://www.themoviedb.org/ "TheTVDB Website") (e.g. it’s a DVD special, behind the scenes, goof reel, etc.), you can instead add the item as an “extra” for the show. See our [article about TV extras](https://support.plex.tv/articles/local-files-for-tv-show-trailers-and-extras/).

**Related Page**: [Local Files for TV Show Trailers and Extras](https://support.plex.tv/articles/local-files-for-tv-show-trailers-and-extras/)

## Multiple Episodes in a Single File

If a single file covers more than one episode, name it using the following format:

- /TV Shows/ShowName/Season 02/ShowName – sXXeYY-eZZ – Optional\_Info.ext

Where you specify the appropriate season, episode numbers (the first and last episode covered in the file), and file extension. For example, `s02e18-e19`.

**Note**: Multi-episode files will show up individually in Plex apps when viewing your library, but playing any of the represented episodes will play the full file. If you want episodes to behave truly independently, you’re best off using a tool to split the file into individual episodes.

To get a better overall experience, we recommend that you use a tool to split the video so that each episode has its own individual file. There are multiple ways you can do this and a quick search in your favorite search engine should give you some options on how to “split” a file. An unofficial guide with one free tool has even been posted in our forums.

**Related Page**: [Forums: Splitting multi-episode files with MKVtoolnix GUI](https://forums.plex.tv/t/howto-splitting-multi-episode-files-with-mkvtoolnix-gui/108396/1)

## Episodes Split Across Multiple Files

**Warning!**: While Plex does have limited support for content split across multiple files, it is not the expected way to handle content. Doing this may negatively impact usage of various Plex features (including, but not limited to, preview thumbnails, skip intro, audio/subtitle stream selection across parts, and more). We recommend users instead join the files together (see below).

Episodes that are split into several files (e.g. pt1, pt2), can be played back as a single file if named correctly. Name the files as follows:

- /TV Shows/ShowName/Season 02/ShowName – s02e17 – Split\_Name.ext

Where `Split_Name` is one of the following:

- cdX
- discX
- diskX
- dvdX
- partX
- ptX

…and you replace `X` with the appropriate number (cd1, cd2, etc.).

**Notes**:

- Not all Plex apps support playback of media split across multiple files
- All parts must be of the same file format (e.g. all MP4 or all MKV)
- All parts should have identical audio and subtitle streams in the same order
- Only stacks up to 8 parts are supported
- Not all features will work correctly when using “split” files.

To get a better overall experience, *we strongly encourage* you to instead use a tool to join/merge the individual files into a single video. There are multiple ways you can do this and a quick search in your favorite search engine should give you some options on how to “join” files. An unofficial guide with one free tool has even been posted in our forums.

**Related Page**: [Forums: Joining multi-part movie files with MKVtoolnix GUI](https://forums.plex.tv/t/howto-joining-multi-part-movies-files-with-mkvtoolnix-gui/113211/1)

## Examples

**Note**: This example illustrates many of the types of content outlined previously. When creating the TV library, it is the `/TV Shows` directory that would be specified as the content location for the library.

```js
/TV Shows
   /Doctor Who (1963)
      /Season 01
         Doctor Who (1963) - s01e01 - An Unearthly Child (1).mp4
         Doctor Who (1963) - s01e02 - The Cave of Skulls (2).mp4
   /From the Earth to the Moon (1998)
      /Season 01
         From the Earth to the Moon (1998) - s01e01.mp4
         From the Earth to the Moon (1998) - s01e02.mp4
   /Grey's Anatomy (2005)
      /Season 00
         Grey's Anatomy (2005) - s00e01 - Straight to the Heart.mkv
      /Season 01
         Grey's Anatomy (2005) - s01e01 - pt1.avi
         Grey's Anatomy (2005) - s01e01 - pt2.avi
         Grey's Anatomy (2005) - s01e02 - The First Cut is the Deepest.avi
         Grey's Anatomy (2005) - s01e03.mp4
      /Season 02
         Grey's Anatomy (2005) - s02e01-e03.avi
         Grey's Anatomy (2005) - s02e04.m4v
   /The Colbert Report (2005)
      /Season 08
         The Colbert Report (2005) - 2011-11-15 - Elijah Wood.avi
   /The Office (UK) (2001) {tmdb-2996}
      /Season 01
         The Office (UK) - s01e01 - Downsize.mp4
   / The Office (US) (2005) {tvdb-73244}
      /Season 01
         The Office (US) - s01e01 - Pilot.mkv
```