#include "GameDelegates.h"
#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Modules/ModuleManager.h"

DEFINE_LOG_CATEGORY_STATIC(LogFDSEditor, Log, All);

// The cooker skips maps in DirectoriesToAlwaysCook, so every map FDS serves must be named to the cook. Passing them
// all on -Map= overruns the editor's command line (about 4K characters crash it at startup), so the cook takes them
// from Config/FDSVenue*.txt here: the venue worlds (FDSVenueMaps.txt) and the sublevel stubs (FDSVenueSublevels-*.txt).
class FFDSEditorModule : public FDefaultModuleImpl
{
public:
    virtual void StartupModule() override
    {
        FGameDelegates::Get().GetCookModificationDelegate().BindLambda([](TArray<FString>& ExtraPackagesToCook)
        {
            const FString ConfigDir = FPaths::ProjectConfigDir();
            TArray<FString> Lists;
            IFileManager::Get().FindFiles(Lists, *(ConfigDir / TEXT("FDSVenue*.txt")), true, false);
            for (const FString& List : Lists)
            {
                TArray<FString> Lines;
                FFileHelper::LoadFileToStringArray(Lines, *(ConfigDir / List));
                for (const FString& Line : Lines)
                {
                    const FString Package = Line.TrimStartAndEnd();
                    if (!Package.IsEmpty())
                    {
                        ExtraPackagesToCook.AddUnique(Package);
                    }
                }
            }
            UE_LOG(LogFDSEditor, Display, TEXT("FDSEditor: %d maps to cook from Config/FDSVenue*.txt (%d lists)"), ExtraPackagesToCook.Num(), Lists.Num());
        });
    }

    virtual void ShutdownModule() override
    {
        FGameDelegates::Get().GetCookModificationDelegate().Unbind();
    }
};

IMPLEMENT_MODULE(FFDSEditorModule, FDSEditor);
