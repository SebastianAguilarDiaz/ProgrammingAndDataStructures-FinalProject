#include<iostream>
#include<fstream>
#include <unordered_map>
#include <vector>
#include<set>
#include<stack>
#include<queue>

using namespace std;
const char PATH_CHAR='1';
const char WALL_CHAR='0';
const char ROOT_CHAR='A';
const char TARGET_CHAR='B';
pair<int,int> root_cords;
pair<int,int> target_cords;

vector<pair<int,int>> movements={
    {0,0},
    {-1,0},
    {0,-1},
    {1,0},
    {0,1}
};



class Node{
    public:
        Node* parent=nullptr;
        pair<int,int>value;
        vector<Node*> children;

        Node(pair<int,int> v){
            value=v;
        }

        Node* addChildren(Node* c){
            this->children.push_back(c);
            c->parent=this;
            return c;
        }
        Node* addParent(Node* p){
            this->parent=p;
            p->children.push_back(this);
            return p;
        }

        vector<Node*>* ancesters(){
        
            auto p=this->parent;
            auto antrs= new vector<Node*>;
            while(p!=nullptr){
                    antrs->push_back(p);
                p=p->parent;
            }
            return antrs;
        }

        vector<Node*>* preorder(){
            auto res= new vector<Node*>;
            res->push_back(this);
            for(auto child: children){
                auto x=child->preorder();
                for (auto m: *x){
                    res->push_back(m);
                }
            }
            return res; 
        }

        vector<Node*>* postorder(){
            auto res=new vector<Node*>;
            for(auto child:children){
                auto x=child->preorder();
                for(auto m:*x)
                    res->push_back(m);
            }
            res->push_back(this);
            return res;
        }


        void print(){
            cout<<"vaule "<<value.first<<" "<<value.second<<" children "<<children.size()<<endl;
        }

};

vector<vector<char>>* loadMatrix(string file_path);

inline int getNodeNumber(pair<int,int> cords, const int COLS);
inline pair<int,int> getNodeCords( int num, const int COLS);
pair<int,int> findNode(vector<vector<char>>*matrix,const char TARGET);
int getNumOfPathChars(vector<vector<char>>*matrix);
vector<pair<int,int>>* dfs(vector<vector<char>>*matrix);

unordered_map<int,Node*>* createTree(vector<vector<char>> *matrix);

int main() {

    auto matrix= loadMatrix("laberinto_1.in");
    root_cords=findNode(matrix,ROOT_CHAR);
    target_cords=findNode(matrix,TARGET_CHAR);
    auto tree= createTree(matrix);



    for(auto n: *tree){
        //n.second->print();
    }

    auto pre=(*tree)[getNodeNumber(root_cords,(*matrix)[0].size())]->postorder();
    for(auto n: *pre) n->print();

    
    




}


vector<vector<char>> *loadMatrix(string file_path){
    ifstream fin(file_path);
    int rows, cols;
    fin>>rows>>cols;
    auto m= new vector<vector<char> >(rows,vector<char>(cols));
    for(auto &i: *m){
        for(auto &j:i){
            fin>>j;
        }
    }
    return m;
}

inline int getNodeNumber(pair<int,int> cords, const int COLS){
    
    if(cords.first<0 || cords.second<0) return -1;
    return cords.first*COLS + cords.second;

}

inline pair<int,int> getNodeCords( int num, const int COLS){

    return {num/COLS,num%COLS};
}




pair<int,int> findNode(vector<vector<char>>*matrix,const char TARGET){

    const int ROWS=(*matrix).size();
    const int COLS=(*matrix)[0].size();

    pair<int,int> root={-1,-1};
    // so we can know where the root node is
    for(int i =0; i < ROWS;i++){
        for(int j =0;j<COLS;j++){   
            if((*matrix)[i][j]==TARGET){
                root={i,j}; 
                break;
            }
        }
    }

    return root;
}




int getNumOfPathChars(vector<vector<char>>*matrix){
    int n=0;
    for(auto i: *matrix){
        for(auto j: i){
            if(j==PATH_CHAR)n++;
        }
    }
    return n;
}





unordered_map<int,Node*>* createTree(vector<vector<char>> *matrix){
    auto root = root_cords;
    const int COLS=(*matrix)[0].size();
    auto r=getNodeNumber(root,COLS);
    
    auto tree = new unordered_map<int,Node*>;
    tree->insert({r,new Node(root)});

    queue<pair<int,int>> bfs_q;
    bfs_q.push(root);
    set<pair<int,int>>visited;
    visited.insert(root); 
    vector<Node*>parents;
    vector<Node*>children;
    while(bfs_q.size()){
            visited.insert(bfs_q.front());
            //tree->insert({getNodeNumber(bfs_q.front(),COLS),new Node(bfs_q.front())});       
            
            for(auto &n: movements){
                pair<int,int> child={bfs_q.front().first + n.first,bfs_q.front().second+n.second};
                if(visited.find(child)!=visited.end()) continue;
                if(
                    child.first <0||
                    child.second <0||
                    child.first >= (*matrix).size()||
                    child.second >= (*matrix)[0].size()
                        )   continue;
                if((*matrix)[child.first][child.second]==PATH_CHAR
                    ||(*matrix)[child.first][child.second]==TARGET_CHAR
                        ){
                    //parents.push_back((*tree)[getNodeNumber(bfs_q.front(),COLS)]);
                    
                    tree->insert({getNodeNumber(child,COLS),new Node(child)});
                    (*tree)[getNodeNumber(bfs_q.front(),COLS)]->addChildren((*tree)[getNodeNumber(child,COLS)]);
                    
                    bfs_q.push(child);}

            }
            bfs_q.pop();
    }


    return tree;
}


